#!/bin/bash
set -u

# Wait for the local Gemini Display web server
for _ in $(seq 1 30); do
  curl -sf http://127.0.0.1:8000/api/settings >/dev/null 2>&1 && break
  sleep 1
done

exec chromium \
  --kiosk \
  --noerrdialogs \
  --no-first-run \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-extensions \
  --force-device-scale-factor=1.0 \
  --use-fake-ui-for-media-stream \
  --autoplay-policy=no-user-gesture-required \
  --password-store=basic \
  "http://127.0.0.1:8000"
