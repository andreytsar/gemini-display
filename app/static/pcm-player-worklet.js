const INV_32768 = 1 / 32768.0;
const IS_LITTLE_ENDIAN = new Uint8Array(new Uint16Array([1]).buffer)[0] === 1;

class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputRate = 24000;
    this.ratio = this.inputRate / sampleRate; // input samples per output sample

    // Zero-copy Int16Array chunk queue: handles long responses (up to 5 min buffered, 0 MB idle)
    // without fixed ring-buffer overflow or per-sample decoding spikes on the audio thread.
    this.chunks = [];
    this.head = 0;
    this.offset = 0;
    this.available = 0;
    this.frac = 0;
    this.maxQueuedSamples = this.inputRate * 300; // 5 minutes safety cap (~14.4 MB worst case)

    // Pre-buffer 240ms initially and 220ms after an underrun to absorb network/generation jitter cleanly
    this.initialPrebufferSamples = Math.floor(this.inputRate * 0.24);
    this.rebufferSamples = Math.floor(this.inputRate * 0.22);
    this.targetPrebuffer = this.initialPrebufferSamples;
    this.playing = false;
    this.gain = 0;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (!msg) return;

      if (msg.type === "config" && msg.sampleRate) {
        this.inputRate = msg.sampleRate;
        this.ratio = this.inputRate / sampleRate;
        this.maxQueuedSamples = this.inputRate * 300;
        this.initialPrebufferSamples = Math.floor(this.inputRate * 0.24);
        this.rebufferSamples = Math.floor(this.inputRate * 0.22);
        if (!this.playing) {
          this.targetPrebuffer = this.initialPrebufferSamples;
        }
        return;
      }

      if (msg.type === "flush_start") {
        if (!this.playing && this.available >= 2) {
          this.playing = true;
          this.port.postMessage({ type: "state", playing: true });
        }
        this.targetPrebuffer = this.initialPrebufferSamples;
        return;
      }

      if (msg.type === "clear") {
        this._resetQueue();
        this.frac = 0;
        this.playing = false;
        this.gain = 0;
        this.targetPrebuffer = this.initialPrebufferSamples;
        this.port.postMessage({ type: "state", playing: false });
        return;
      }

      if (msg.type === "audio" && msg.pcm) {
        const sampleCount = msg.pcm.byteLength >> 1;
        if (sampleCount === 0) return;

        let samples;
        if (IS_LITTLE_ENDIAN) {
          // O(1) zero-copy view over transferred ArrayBuffer
          samples = new Int16Array(msg.pcm, 0, sampleCount);
        } else {
          const view = new DataView(msg.pcm, 0, sampleCount * 2);
          samples = new Int16Array(sampleCount);
          for (let i = 0; i < sampleCount; i++) {
            samples[i] = view.getInt16(i * 2, true);
          }
        }

        this.chunks.push(samples);
        this.available += sampleCount;

        // Guard against unbounded memory in pathological cases (>5 min queued)
        while (this.available > this.maxQueuedSamples && this.head < this.chunks.length) {
          const dropped = this.chunks[this.head].length - this.offset;
          this.chunks[this.head] = null;
          this.head++;
          this.offset = 0;
          this.available -= dropped;
        }
        if (this.head >= 64) {
          this.chunks = this.chunks.slice(this.head);
          this.head = 0;
        }

        if (!this.playing && this.available >= this.targetPrebuffer) {
          this.playing = true;
          this.port.postMessage({ type: "state", playing: true });
        }
      }
    };
  }

  _resetQueue() {
    this.chunks = [];
    this.head = 0;
    this.offset = 0;
    this.available = 0;
  }

  process(_inputs, outputs) {
    const output = outputs[0];
    if (!output || output.length === 0) return true;
    const ch0 = output[0];

    if (!this.playing) {
      ch0.fill(0);
      for (let c = 1; c < output.length; c++) output[c].fill(0);
      return true;
    }

    for (let i = 0; i < ch0.length; i++) {
      if (this.available < 2) {
        // Smooth fade-out on buffer drain to avoid clicks
        this.gain = Math.max(0, this.gain - 0.02);
        const last =
          this.available === 1 && this.head < this.chunks.length
            ? this.chunks[this.head][this.offset] * INV_32768
            : 0;
        ch0[i] = last * this.gain;
        if (this.gain === 0 && this.playing) {
          this.playing = false;
          this.frac = 0;
          this._resetQueue();
          this.targetPrebuffer = this.rebufferSamples;
          this.port.postMessage({ type: "state", playing: false });
        }
        continue;
      }

      // Smooth fade-in to avoid clicks on utterance start
      if (this.gain < 1) {
        this.gain = Math.min(1, this.gain + 0.01);
      }

      const curChunk = this.chunks[this.head];
      const s0 = curChunk[this.offset];
      const s1 =
        this.offset + 1 < curChunk.length
          ? curChunk[this.offset + 1]
          : this.chunks[this.head + 1][0];

      const sample = (s0 + (s1 - s0) * this.frac) * INV_32768;
      let out = sample * this.gain;
      if (out > 0.98) out = 0.98;
      else if (out < -0.98) out = -0.98;
      ch0[i] = out;

      this.frac += this.ratio;
      const advance = this.frac | 0;
      if (advance > 0) {
        this.frac -= advance;
        let step = advance < this.available ? advance : this.available;
        this.available -= step;
        while (step > 0 && this.head < this.chunks.length) {
          const rem = this.chunks[this.head].length - this.offset;
          if (step < rem) {
            this.offset += step;
            step = 0;
          } else {
            step -= rem;
            this.chunks[this.head] = null;
            this.head++;
            this.offset = 0;
          }
        }
      }
    }

    if (this.head >= 64) {
      this.chunks = this.chunks.slice(this.head);
      this.head = 0;
    }

    // Mirror mono speech cleanly to stereo channels
    for (let c = 1; c < output.length; c++) {
      output[c].set(ch0);
    }
    return true;
  }
}

registerProcessor("pcm-player-processor", PcmPlayerProcessor);
