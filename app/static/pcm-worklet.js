class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.ratio = sampleRate / this.targetRate;
    this.accSum = 0;
    this.accCount = 0;
    this.phase = 0;
    this.outBuffer = new Int16Array(1600); // 100 ms at 16 kHz
    this.outIndex = 0;
    this.levelSq = 0;
    this.levelCount = 0;
    this.muted = false;

    // Gentle noise gate: suppress steady low-level room hiss so ASR doesn't hallucinate foreign words
    this.gateThreshold = 0.008;
    this.hangoverFrames = 4; // 400 ms hangover after speech drops below threshold
    this.hangoverLeft = 0;

    this.port.onmessage = (event) => {
      if (event.data && typeof event.data.muted === "boolean") {
        this.muted = event.data.muted;
      }
    };
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0 || !input[0]) {
      return true;
    }
    const channel = input[0];
    if (this.muted) {
      return true;
    }

    // Box-averaged anti-aliased decimation to 16 kHz
    for (let i = 0; i < channel.length; i++) {
      this.accSum += channel[i];
      this.accCount++;
      this.phase += 1;

      if (this.phase >= this.ratio) {
        const sample = this.accSum / this.accCount;
        this.accSum = 0;
        this.accCount = 0;
        this.phase -= this.ratio;

        this.levelSq += sample * sample;
        this.levelCount++;

        const clamped = Math.max(-1, Math.min(1, sample));
        this.outBuffer[this.outIndex++] = clamped < 0 ? Math.round(clamped * 32768) : Math.round(clamped * 32767);

        if (this.outIndex >= this.outBuffer.length) {
          const rms = this.levelCount > 0 ? Math.sqrt(this.levelSq / this.levelCount) : 0;
          let copy;
          if (rms >= this.gateThreshold) {
            this.hangoverLeft = this.hangoverFrames;
            copy = this.outBuffer.slice(0);
          } else if (this.hangoverLeft > 0) {
            this.hangoverLeft--;
            copy = this.outBuffer.slice(0);
          } else {
            copy = new Int16Array(this.outBuffer.length); // clean digital silence
          }
          const displayLevel = rms >= this.gateThreshold ? Math.min(1, rms * 4.5) : 0;
          this.port.postMessage({ pcm: copy.buffer, level: displayLevel }, [copy.buffer]);
          this.outIndex = 0;
          this.levelSq = 0;
          this.levelCount = 0;
        }
      }
    }

    return true;
  }
}

registerProcessor("pcm-capture-processor", PcmCaptureProcessor);
