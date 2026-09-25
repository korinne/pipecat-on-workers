import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { PlaybackQueue, pcm16FromBase64, pcm16ToBase64 } from './audio-player.mjs';

class FakeContext {
  state = 'running';
  currentTime = 0;
  destination = {};
  created = [];
  createBuffer(channels, length, sampleRate) { return { length, sampleRate, copyToChannel() {} }; }
  createBufferSource() {
    const source = { connect() {}, disconnect() {}, stop() { this.stopped = true; }, start(time, offset, duration) { this.startTime = time; this.endTime = time + duration; this.offset = offset; } };
    this.created.push(source);
    return source;
  }
  tick(seconds) {
    this.currentTime += seconds;
    for (const source of this.created) if (!source.stopped && !source.ended && source.endTime <= this.currentTime) { source.ended = true; source.onended?.(); }
  }
}
function packet(generation = 0, chunk_id = 1, seconds = .5) {
  return { generation, chunk_id, data: pcm16ToBase64(new Int16Array(Math.round(16000 * seconds)).buffer), sample_rate: 16000 };
}
function setup() {
  const context = new FakeContext();
  const played = [];
  const player = new PlaybackQueue(context, { autoPump: false, onPlayed: chunk => played.push(chunk) });
  return { context, played, player };
}
test('PCM is explicitly little endian and preserves signed extrema', () => {
  const encoded = pcm16ToBase64(new Int16Array([-32768, -1, 0, 32767]).buffer);
  assert.equal(encoded, 'AID//wAA/38=');
  assert.deepEqual(Array.from(pcm16FromBase64(encoded)), [-1, -1 / 32768, 0, 32767 / 32768]);
  assert.throws(() => pcm16FromBase64('AQ=='), /Invalid/);
});
test('schedules a bounded horizon and acknowledges only a wholly played chunk', () => {
  const { context, played, player } = setup();
  player.enqueue(packet());
  assert.equal(context.created.length, 3);
  assert.ok(player.nextTime <= .415);
  context.tick(.32);
  assert.deepEqual(played, []);
  player.pump();
  context.tick(.3);
  assert.deepEqual(played, [{ generation: 0, chunk_id: 1 }]);
});
test('interruption cancels queued and scheduled audio without acknowledging it', () => {
  const { context, played, player } = setup();
  player.enqueue(packet());
  context.tick(.15);
  player.clear();
  assert.equal(player.hasPending, false);
  assert.equal(player.pendingSeconds, 0);
  assert.ok(context.created.filter(x => !x.ended).every(x => x.stopped));
  context.tick(1);
  assert.deepEqual(played, []);
  assert.equal(player.enqueue(packet(0, 2)), false);
  assert.equal(player.enqueue(packet(1, 1)), true);
});
test('clear generation is a minimum accepted new generation', () => {
  const { player } = setup();
  player.enqueue(packet(3));
  player.clear(4);
  assert.equal(player.enqueue(packet(3, 2)), false);
  assert.equal(player.enqueue(packet(4, 1)), true);
  player.clear(3);
  assert.equal(player.enqueue(packet(3, 3)), false);
});
test('a newer generation stops older sources and old events cannot ack', () => {
  const { context, played, player } = setup();
  player.enqueue(packet(0));
  const staleEnd = context.created[0].onended;
  player.enqueue(packet(1, 2));
  staleEnd();
  assert.deepEqual(played, []);
  assert.equal(player.enqueue(packet(0, 3)), false);
});
test('duplicate packets never schedule or acknowledge twice', () => {
  const { context, player } = setup();
  player.enqueue(packet(0, 10, .1));
  assert.equal(player.enqueue(packet(0, 10, .1)), false);
  assert.equal(context.created.length, 1);
});
test('buffer and malformed-packet bounds fail explicitly', () => {
  const { player } = setup();
  player.enqueue(packet(0, 1, 6));
  assert.throws(() => player.enqueue(packet(0, 2, 7)), /exceeded 12 seconds/);
  assert.throws(() => player.enqueue({ ...packet(0, 3), sample_rate: 1 }), /Invalid audio packet/);
});
test('closing prevents future playback or acknowledgments', () => {
  const { context, player, played } = setup();
  player.enqueue(packet());
  player.close();
  context.tick(2);
  assert.equal(player.enqueue(packet(1)), false);
  assert.deepEqual(played, []);
});
for (const sampleRate of [16000, 44100, 48000]) {
  test(`capture resamples actual ${sampleRate} Hz into 16 kHz / 20 ms frames`, () => {
    const messages = [];
    let Processor;
    const sandbox = {
      sampleRate,
      AudioWorkletProcessor: class { port = { postMessage(message) { messages.push(message); } }; },
      registerProcessor(name, klass) { Processor = klass; },
    };
    vm.runInNewContext(fs.readFileSync(new URL('./capture-processor.js', import.meta.url), 'utf8'), sandbox);
    const processor = new Processor();
    for (let offset = 0; offset < sampleRate; offset += 128) processor.process([[new Float32Array(Math.min(128, sampleRate - offset)).fill(.25)]]);
    assert.equal(messages.length, 50);
    assert.equal(messages[0].pcm.byteLength, 640);
    assert.ok(Math.abs(messages[0].rms - .25) < 1e-6);
    assert.equal(new Int16Array(messages[0].pcm)[0], 8192);
  });
}
