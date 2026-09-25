// Integrate input samples over fractional windows. The AudioWorklet's sampleRate
// is the actual hardware/context rate, including 44.1 kHz and 48 kHz devices.
class PCM16Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / 16000;
    this.remaining = this.ratio;
    this.integral = 0;
    this.frame = new Int16Array(320); // 20 ms at the wire rate.
    this.index = 0;
    this.energy = 0;
  }
  process(inputs) {
    const samples = inputs[0]?.[0];
    if (!samples) return true;
    for (const sample of samples) {
      let left = 1;
      while (left > 1e-8) {
        const weight = Math.min(left, this.remaining);
        this.integral += sample * weight;
        this.remaining -= weight;
        left -= weight;
        if (this.remaining < 1e-8) {
          const value = Math.max(-1, Math.min(1, this.integral / this.ratio));
          this.frame[this.index++] = Math.round(value * (value < 0 ? 32768 : 32767));
          this.energy += value * value;
          this.remaining = this.ratio;
          this.integral = 0;
          if (this.index === this.frame.length) {
            this.port.postMessage({ pcm: this.frame.buffer, rms: Math.sqrt(this.energy / this.frame.length) }, [this.frame.buffer]);
            this.frame = new Int16Array(320);
            this.index = 0;
            this.energy = 0;
          }
        }
      }
    }
    return true;
  }
}
registerProcessor('pcm16-capture', PCM16Capture);
