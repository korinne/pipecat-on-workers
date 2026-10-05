import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { options, ReceiptQueue, safeDiagnostics } from './check_real_voice.mjs';

const packet = (generation, chunk_id, samples = 2400, text = '') => ({
  generation, chunk_id, sample_rate: 24000, text, data: Buffer.alloc(samples * 2, 1).toString('base64'),
});

test('default plan is twenty turns; supported plans never exceed twenty-four', () => {
  const defaultPlan = options([]);
  assert.equal(defaultPlan.duration, 600000);
  assert.equal(defaultPlan.rounds * defaultPlan.sessions, 20);
  for (const sessions of ['1', '2']) for (const duration of ['180000', '600000', '720000']) {
    const plan = options(['--duration-ms', duration, '--sessions', sessions]);
    assert.ok(plan.rounds * plan.sessions <= 24);
  }
  assert.throws(() => options(['--sessions', '3']), /sessions_must_be/);
  assert.throws(() => options(['--duration-ms', 'Infinity']), /duration_must_be/);
  assert.throws(() => options(['--duration-ms', '1000']), /duration_must_be/);
  assert.throws(() => options(['--token', 'secret']), /invalid_arguments/);
  assert.equal(options(['--pcm-second', 'second.pcm']).pcmSecond, 'second.pcm');
});

test('receipts wait for serial PCM duration and preserve the final sentence marker', () => {
  const q = new ReceiptQueue();
  q.enqueue(packet(4, 1), 1000);
  q.enqueue(packet(4, 2, 1200, 'Private sentence.'), 1001);
  assert.deepEqual(q.due(1099), []);
  assert.deepEqual(q.due(1100).map(v => v.chunk_id), [1]);
  assert.deepEqual(q.due(1149), []);
  const last = q.due(1150);
  assert.equal(last[0].chunk_id, 2);
  assert.equal(last[0].text, 'Private sentence.');
  assert.equal(last[0].duration, 50);
});

test('interruption drops outstanding receipts and ignores late old-generation audio', () => {
  const q = new ReceiptQueue();
  q.enqueue(packet(4, 1), 1000);
  q.clear(5, 1050);
  assert.equal(q.enqueue(packet(4, 2), 1060), null);
  assert.deepEqual(q.due(2000), []);
  q.enqueue(packet(5, 1), 2000);
  q.clear(4, 2050); // An old clear cannot erase new playback.
  assert.equal(q.due(2100)[0].generation, 5);
  assert.throws(() => q.enqueue(packet(5, 1), 2200), /duplicate_audio_packet/);
});

test('output bounds reject invalid PCM and more than twelve seconds queued', () => {
  const q = new ReceiptQueue();
  assert.throws(() => q.enqueue(packet(1, 1, 2401), 0), /invalid_pcm_output/);
  assert.throws(() => q.enqueue({ ...packet(1, 1), sample_rate: 16000 }, 0), /invalid_audio_packet/);
  const bounded = new ReceiptQueue();
  for (let i = 0; i < 120; i++) bounded.enqueue(packet(1, i), 0);
  assert.throws(() => bounded.enqueue(packet(1, 120), 0), /playback_queue_bound_exceeded/);
});

test('public diagnostics exclude transcripts, identifiers, URLs, errors and unknown fields', () => {
  const result = safeDiagnostics({ closed: true, pipecat_tasks: 0, provider_sockets: 0,
    messages: 5, token: 'secret', id: 'session-id', history: [{ text: 'private' }],
    pending_turn_tasks: 0, pending_user_fragments: 0, pending_user_chars: 0, turn_timeout_secs: 5,
    metrics: [{ event: 'error', message: 'private secret' }], provider_readers: 'secret', url: 'https://host?token=secret' });
  assert.deepEqual(result, { closed: true, pipecat_tasks: 0, provider_sockets: 0,
    pending_turn_tasks: 0, pending_user_fragments: 0, pending_user_chars: 0, messages: 5, turn_timeout_secs: 5 });
  assert.ok(!JSON.stringify(result).includes('secret'));
});

test('input-only mode validates both recordings without credentials or network setup', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'real-voice-harness-test-'));
  try {
    const first = path.join(dir, 'first.pcm'), second = path.join(dir, 'second.pcm');
    await fs.writeFile(first, Buffer.alloc(6400, 3)); await fs.writeFile(second, Buffer.alloc(9600, 5));
    const env = { ...process.env }; delete env.DEMO_ACCESS_KEY;
    const script = fileURLToPath(new URL('./check_real_voice.mjs', import.meta.url));
    const child = spawnSync(process.execPath, [script, '--pcm', first, '--pcm-second', second, '--validate-input'], { env, encoding: 'utf8', timeout: 5000 });
    assert.equal(child.status, 0, child.stderr);
    const result = JSON.parse(child.stdout);
    assert.equal(result.network_used, false);
    assert.deepEqual(result.input_duration_ms, [200, 300]);
    assert.equal(result.planned_input_turns, 20);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});
