export function pcm16FromBase64(data) {
  const raw = atob(data);
  if (!raw.length || raw.length % 2) throw new Error('Invalid PCM16 audio payload.');
  const samples = new Float32Array(raw.length / 2);
  for (let i = 0; i < samples.length; i++) {
    let value = raw.charCodeAt(i * 2) | raw.charCodeAt(i * 2 + 1) << 8;
    if (value >= 32768) value -= 65536;
    samples[i] = value / 32768;
  }
  return samples;
}

export function pcm16ToBase64(buffer) {
  const samples = new Int16Array(buffer);
  let bytes = '';
  // Explicit little endian; do not depend on host endianness.
  for (const value of samples) bytes += String.fromCharCode(value & 255, value >> 8 & 255);
  return btoa(bytes);
}

// Only a short horizon is scheduled into Web Audio. The remaining queue is
// bounded and all scheduled sources can be stopped synchronously on barge-in.
export class PlaybackQueue {
  constructor(context, { onPlayed = () => {}, onChange = () => {}, onFirstAudio = () => {}, autoPump = true } = {}) {
    this.context = context;
    this.onPlayed = onPlayed;
    this.onChange = onChange;
    this.onFirstAudio = onFirstAudio;
    this.floor = 0;
    this.generation = -1;
    this.queue = [];
    this.sources = new Set();
    this.chunks = new Set();
    this.seen = new Set();
    this.nextTime = 0;
    this.closed = false;
    this.timer = autoPump ? setInterval(() => this.pump(), 25) : null;
  }
  get pendingSeconds() {
    return Math.max(0, this.nextTime - this.context.currentTime) + this.queue.reduce((sum, chunk) => sum + (chunk.buffer.length - chunk.offset) / chunk.buffer.sampleRate, 0);
  }
  get hasPending() { return this.queue.length > 0 || this.sources.size > 0; }
  enqueue({ data, sample_rate, generation, chunk_id }) {
    if (this.closed || !Number.isSafeInteger(generation) || generation < this.floor || generation < this.generation) return false;
    if (!Number.isSafeInteger(chunk_id) || !Number.isSafeInteger(sample_rate) || sample_rate < 8000 || sample_rate > 48000 || typeof data !== 'string' || data.length > 2_000_000) throw new Error('Invalid audio packet.');
    if (generation > this.generation) {
      this.clear(generation);
      this.generation = generation;
    }
    const key = `${generation}:${chunk_id}`;
    if (this.seen.has(key)) return false;
    const samples = pcm16FromBase64(data);
    if (this.pendingSeconds + samples.length / sample_rate > 12) throw new Error('Playback queue exceeded 12 seconds. Response interrupted to release audio.');
    const buffer = this.context.createBuffer(1, samples.length, sample_rate);
    buffer.copyToChannel(samples, 0);
    const chunk = { generation, chunk_id, buffer, offset: 0, remaining: 0, cancelled: false };
    this.seen.add(key);
    this.chunks.add(chunk);
    this.queue.push(chunk);
    this.pump();
    return true;
  }
  pump() {
    if (this.closed || this.context.state !== 'running') return;
    const horizon = this.context.currentTime + .3;
    while (this.queue.length && this.nextTime < horizon) {
      const chunk = this.queue[0];
      const frames = Math.min(chunk.buffer.length - chunk.offset, Math.round(chunk.buffer.sampleRate * .1));
      const duration = frames / chunk.buffer.sampleRate;
      const start = Math.max(this.context.currentTime + .015, this.nextTime);
      const source = this.context.createBufferSource();
      source.buffer = chunk.buffer;
      source.connect(this.context.destination);
      chunk.remaining++;
      this.sources.add(source);
      source.onended = () => {
        source.disconnect();
        this.sources.delete(source);
        chunk.remaining--;
        if (!chunk.cancelled && chunk.remaining === 0 && chunk.offset === chunk.buffer.length) {
          this.chunks.delete(chunk);
          if (!this.closed && chunk.generation >= this.floor && chunk.generation === this.generation) this.onPlayed({ generation: chunk.generation, chunk_id: chunk.chunk_id });
        }
        this.onChange(this.pendingSeconds);
      };
      source.start(start, chunk.offset / chunk.buffer.sampleRate, duration);
      if (chunk.offset === 0) this.onFirstAudio({ generation: chunk.generation, chunk_id: chunk.chunk_id, scheduledAt: start });
      chunk.offset += frames;
      this.nextTime = start + duration;
      if (chunk.offset === chunk.buffer.length) this.queue.shift();
    }
    this.onChange(this.pendingSeconds);
  }
  clear(minGeneration = this.generation + 1) {
    const before = performance.now();
    this.floor = Math.max(this.floor, minGeneration);
    for (const chunk of this.chunks) chunk.cancelled = true;
    for (const source of this.sources) {
      source.onended = null;
      try { source.stop(); } catch { /* A source can already have ended. */ }
      source.disconnect();
    }
    this.sources.clear();
    this.queue.length = 0;
    this.chunks.clear();
    this.seen.clear();
    this.nextTime = this.context.currentTime;
    this.onChange(0);
    return performance.now() - before;
  }
  close() { this.clear(); this.closed = true; clearInterval(this.timer); }
}
