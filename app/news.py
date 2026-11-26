from __future__ import annotations

import html
import logging
import re
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Google News edition parameters: (hl, gl, ceid)
EDITIONS: dict[str, tuple[str, str, str]] = {
    "il_en": ("en-IL", "IL", "IL:en"),
    "il_he": ("he", "IL", "IL:he"),
    "world": ("en-US", "US", "US:en"),
    "us": ("en-US", "US", "US:en"),
    "en": ("en-US", "US", "US:en"),
    "he": ("he", "IL", "IL:he"),
    "ru": ("ru", "RU", "RU:ru"),
}

CATEGORY_TOPICS: dict[str, str] = {
    "world": "WORLD",
    "nation": "NATION",
    "local": "NATION",
    "israel": "NATION",
    "business": "BUSINESS",
    "finance": "BUSINESS",
    "business & finance": "BUSINESS",
    "tech": "TECHNOLOGY",
    "technology": "TECHNOLOGY",
    "science": "SCIENCE",
    "health": "HEALTH",
    "sports": "SPORTS",
    "entertainment": "ENTERTAINMENT",
}

# Friendly presets for common custom topics so RSS search stays high-signal
TOPIC_SEARCH_OVERRIDES: dict[str, str] = {
    "ai & tech": "artificial intelligence OR technology",
    "ai": "artificial intelligence",
    "astronomy": "astronomy OR telescope OR NASA OR ESA OR exoplanet",
    "space": "space exploration OR NASA OR SpaceX OR astronomy",
}

_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(raw: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", raw or "")).split())


def _extract_related(description_html: str, main_title: str, max_related: int = 2) -> list[str]:
    if not description_html:
        return []
    related: list[str] = []
    main_norm = main_title.strip().lower()
    for match in _LI_RE.findall(description_html):
        text = _clean_html(match)
        if not text:
            continue
        if text.lower().startswith(main_norm[:35]):
            continue
        related.append(text)
        if len(related) >= max_related:
            break
    return related


def _format_age(pub_date: str) -> str:
    if not pub_date:
        return ""
    try:
        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta_s = max(0, int((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()))
        if delta_s < 3600:
            mins = max(1, delta_s // 60)
            return f"{mins}m ago"
        if delta_s < 86400:
            hrs = delta_s // 3600
            return f"{hrs}h ago"
        days = delta_s // 86400
        return f"{days}d ago"
    except Exception:
        return pub_date


def _resolve_edition(edition: str | None, default: str = "world") -> tuple[str, str, str]:
    key = (edition or default).strip().lower().replace("-", "_")
    return EDITIONS.get(key, EDITIONS[default])


class GoogleNewsService:
    """Official Google News RSS client with caching, multi-edition support, and topic briefings."""

    def __init__(self, cache_ttl_seconds: int = 300) -> None:
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()
        self._client = httpx.Client(
            timeout=8.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 Chrome/130.0 Safari/537.36",
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            },
        )

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def _fetch_rss(self, url: str, limit: int = 6, force: bool = False) -> list[dict[str, Any]]:
        now = time.monotonic()
        if not force:
            with self._lock:
                cached = self._cache.get(url)
                if cached and (now - cached[0]) < self._ttl:
                    return cached[1][:limit]

        resp = self._client.get(url)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        items: list[dict[str, Any]] = []
        for el in root.findall("./channel/item"):
            raw_title = (el.findtext("title") or "").strip()
            if not raw_title:
                continue
            source = (el.findtext("source") or "").strip()
            title = raw_title
            if source and title.endswith(f" - {source}"):
                title = title[: -(len(source) + 3)].strip()
            elif not source and " - " in title:
                t_part, s_part = title.rsplit(" - ", 1)
                if len(s_part) <= 45:
                    title, source = t_part.strip(), s_part.strip()

            pub_date = (el.findtext("pubDate") or "").strip()
            desc_html = el.findtext("description") or ""
            related = _extract_related(desc_html, title, max_related=2)

            entry: dict[str, Any] = {
                "title": title,
                "source": source,
                "age": _format_age(pub_date),
            }
            if related:
                entry["related"] = related
            items.append(entry)
            if len(items) >= 25:
                break

        with self._lock:
            self._cache[url] = (now, items)
            # Keep cache bounded
            if len(self._cache) > 64:
                oldest_key = min(self._cache.items(), key=lambda kv: kv[1][0])[0]
                self._cache.pop(oldest_key, None)

        return items[:limit]

    def get_headlines(
        self,
        category: str = "top",
        edition: str = "world",
        limit: int = 6,
    ) -> dict[str, Any]:
        cat_key = (category or "top").strip().lower()
        n = max(1, min(12, int(limit or 6)))

        # If asking for Israel/local news without an explicit IL edition, default to il_en (or il_he if he)
        ed_key = (edition or "world").strip().lower().replace("-", "_")
        if cat_key in ("israel", "local") and ed_key in ("world", "us", "en", ""):
            ed_key = "il_en"
        elif cat_key in ("israel", "local") and ed_key == "he":
            ed_key = "il_he"

        hl, gl, ceid = _resolve_edition(ed_key, default="world")
        if cat_key in ("top", "headlines", "all", ""):
            url = f"https://news.google.com/rss?hl={hl}&gl={gl}&ceid={ceid}"
            label = f"Top headlines ({ed_key})"
        elif cat_key in CATEGORY_TOPICS:
            topic_code = CATEGORY_TOPICS[cat_key]
            url = f"https://news.google.com/rss/headlines/section/topic/{topic_code}?hl={hl}&gl={gl}&ceid={ceid}"
            label = f"{cat_key.title()} ({ed_key})"
        else:
            # Fallback to search if caller passed a custom category like "astronomy"
            return self.search_news(query=category, language="he" if ed_key in ("il_he", "he") else ("ru" if ed_key == "ru" else "en"), days=7, limit=n)

        try:
            stories = self._fetch_rss(url, limit=n)
            return {
                "ok": True,
                "category": cat_key or "top",
                "edition": ed_key,
                "label": label,
                "count": len(stories),
                "stories": stories,
            }
        except Exception as exc:
            logger.warning("Google News get_headlines(%s, %s) failed: %s", cat_key, ed_key, exc)
            return {"ok": False, "error": f"Failed to fetch Google News headlines: {exc}"}

    def search_news(
        self,
        query: str,
        language: str = "en",
        days: int = 7,
        limit: int = 6,
    ) -> dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "Search query is required."}

        n = max(1, min(12, int(limit or 6)))
        d = max(1, min(30, int(days or 7)))
        lang = (language or "en").strip().lower()

        if lang in ("he", "iw", "hebrew", "il_he"):
            hl, gl, ceid = EDITIONS["il_he"]
        elif lang in ("ru", "russian"):
            hl, gl, ceid = EDITIONS["ru"]
        elif lang in ("il_en", "en_il"):
            hl, gl, ceid = EDITIONS["il_en"]
        else:
            hl, gl, ceid = EDITIONS["world"]

        q_expanded = TOPIC_SEARCH_OVERRIDES.get(q.lower(), q)
        full_q = f"{q_expanded} when:{d}d" if "when:" not in q_expanded.lower() else q_expanded
        encoded_q = urllib.parse.quote_plus(full_q)
        url = f"https://news.google.com/rss/search?q={encoded_q}&hl={hl}&gl={gl}&ceid={ceid}"

        try:
            stories = self._fetch_rss(url, limit=n)
            return {
                "ok": True,
                "query": q,
                "language": lang,
                "days": d,
                "count": len(stories),
                "stories": stories,
            }
        except Exception as exc:
            logger.warning("Google News search_news(%r) failed: %s", q, exc)
            return {"ok": False, "error": f"Failed to search Google News: {exc}"}

    def _fetch_topic_entry(self, topic: str, language: str, per_topic: int) -> tuple[str, list[dict[str, Any]]]:
        t_clean = topic.strip()
        t_low = t_clean.lower()

        if t_low in ("israel", "ישראל", "израиль"):
            ed = "il_he" if language == "he" else "il_en"
            res = self.get_headlines(category="top", edition=ed, limit=per_topic)
            return t_clean, res.get("stories", [])

        if t_low in ("israel hebrew", "israel (hebrew)", "il_he"):
            res = self.get_headlines(category="top", edition="il_he", limit=per_topic)
            return t_clean, res.get("stories", [])

        if t_low in CATEGORY_TOPICS:
            ed = "il_he" if language == "he" else ("ru" if language == "ru" else "world")
            res = self.get_headlines(category=t_low, edition=ed, limit=per_topic)
            return t_clean, res.get("stories", [])

        if t_low in ("tech, ai & science", "tech & ai", "ai & tech"):
            # Pull Technology section for high quality
            ed = "il_he" if language == "he" else ("ru" if language == "ru" else "world")
            res = self.get_headlines(category="technology", edition=ed, limit=per_topic)
            return t_clean, res.get("stories", [])

        res = self.search_news(query=t_clean, language=language, days=5, limit=per_topic)
        return t_clean, res.get("stories", [])

    def get_briefing(
        self,
        topics: list[str],
        language: str = "en",
        per_topic: int = 3,
    ) -> dict[str, Any]:
        clean_topics = [t.strip() for t in topics if t and t.strip()]
        if not clean_topics:
            clean_topics = ["Israel", "World", "Technology", "Science", "Business", "Astronomy"]
        clean_topics = clean_topics[:8]
        n = max(1, min(5, int(per_topic or 3)))
        lang = (language or "en").strip().lower()

        # When speaking English, also include a quick peek at Israel (Hebrew) if only "Israel" is listed?
        # Keep topics crisp as configured; S.A.R.A.H. can query il_he explicitly whenever Hebrew news is asked.
        results_by_topic: dict[str, list[dict[str, Any]]] = {}
        with ThreadPoolExecutor(max_workers=min(6, len(clean_topics))) as pool:
            futs = {
                pool.submit(self._fetch_topic_entry, topic, lang, n): topic
                for topic in clean_topics
            }
            for fut in as_completed(futs):
                topic = futs[fut]
                try:
                    t_name, stories = fut.result()
                    results_by_topic[t_name] = stories
                except Exception as exc:
                    logger.warning("Briefing fetch failed for %r: %s", topic, exc)
                    results_by_topic[topic] = []

        ordered_sections = [
            {"topic": t, "stories": results_by_topic.get(t, [])}
            for t in clean_topics
        ]
        total = sum(len(s["stories"]) for s in ordered_sections)
        if total == 0:
            return {"ok": False, "error": "Could not retrieve any Google News stories right now."}

        return {
            "ok": True,
            "language": lang,
            "sections": ordered_sections,
            "total_stories": total,
        }
