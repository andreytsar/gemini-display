class PcmPlayerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.inputRate = 24000;
    this.ratio = this.inputRate / sampleRate; // input samples per output sample

    // Ring buffer holding Float32 samples at inputRate (~12 seconds capacity at 24kHz)
    this.capacity = 24000 * 12;
    this.buffer = new Float32Array(this.capacity);
    this.writePos = 0;
    this.readPos = 0;
    this.available = 0;
    this.frac = 0;

    // Pre-buffer 220ms initially to absorb network jitter cleanly, 100ms after underrun
    this.initialPrebufferSamples = Math.floor(this.inputRate * 0.22);
    this.rebufferSamples = Math.floor(this.inputRate * 0.10);
    this.targetPrebuffer = this.initialPrebufferSamples;
    this.playing = false;
    this.gain = 0;

    this.port.onmessage = (event) => {
      const msg = event.data;
      if (!msg) return;

      if (msg.type === "config" && msg.sampleRate) {
        this.inputRate = msg.sampleRate;
        this.ratio = this.inputRate / sampleRate;
        this.initialPrebufferSamples = Math.floor(this.inputRate * 0.22);
        this.rebufferSamples = Math.floor(this.inputRate * 0.10);
        this.targetPrebuffer = this.initialPrebufferSamples;
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
        this.writePos = 0;
        this.readPos = 0;
        this.available = 0;
        this.frac = 0;
        this.playing = false;
        this.gain = 0;
        this.targetPrebuffer = this.initialPrebufferSamples;
        this.port.postMessage({ type: "state", playing: false });
        return;
      }

      if (msg.type === "audio" && msg.pcm) {
        const bytes = new Uint8Array(msg.pcm);
        const sampleCount = bytes.byteLength >> 1;
        if (sampleCount === 0) return;

        // Read Int16 little-endian safely regardless of byte offset
        const view = new DataView(bytes.buffer, bytes.byteOffset, sampleCount * 2);
        for (let i = 0; i < sampleCount; i++) {
          if (this.available >= this.capacity - 1) {
            // Drop oldest sample on extreme overflow
            this.readPos = (this.readPos + 1) % this.capacity;
            this.available--;
          }
          const s = view.getInt16(i * 2, true) / 32768.0;
          this.buffer[this.writePos] = s;
          this.writePos = (this.writePos + 1) % this.capacity;
          this.available++;
        }

        if (!this.playing && this.available >= this.targetPrebuffer) {
          this.playing = true;
          this.port.postMessage({ type: "state", playing: true });
        }
      }
    };
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
        const last = this.available === 1 ? this.buffer[this.readPos] : 0;
        ch0[i] = last * this.gain;
        if (this.gain === 0 && this.playing) {
          this.playing = false;
          this.frac = 0;
          this.targetPrebuffer = this.rebufferSamples;
          this.port.postMessage({ type: "state", playing: false });
        }
        continue;
      }

      // Smooth fade-in to avoid clicks on utterance start
      if (this.gain < 1) {
        this.gain = Math.min(1, this.gain + 0.01);
      }

      const idx0 = this.readPos;
      const idx1 = (this.readPos + 1) % this.capacity;
      const sample = this.buffer[idx0] + (this.buffer[idx1] - this.buffer[idx0]) * this.frac;
      ch0[i] = Math.max(-0.98, Math.min(0.98, sample * this.gain));

      this.frac += this.ratio;
      const advance = Math.floor(this.frac);
      if (advance > 0) {
        const step = Math.min(advance, this.available);
        this.readPos = (this.readPos + step) % this.capacity;
        this.available -= step;
        this.frac -= advance;
      }
    }

    // Mirror mono speech cleanly to stereo channels
    for (let c = 1; c < output.length; c++) {
      output[c].set(ch0);
    }
    return true;
  }
}

registerProcessor("pcm-player-processor", PcmPlayerProcessor);
