import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { options, PendingCancellation } from './check_real_pending.mjs';

test('only the selected pending status triggers one interrupt, before audio', () => {
  const tool = new PendingCancellation('tool');
  assert.equal(tool.status({ state: 'thinking', generation: 2 }, 10, 0), false);
  assert.equal(tool.status({ state: 'tool', generation: 2 }, 20, 0), true);
  assert.equal(tool.status({ state: 'tool', generation: 2 }, 21, 0), false);
  assert.equal(tool.requestedAt, 20);
  assert.throws(() => new PendingCancellation('thinking').status({ state: 'thinking', generation: 2 }, 0, 1), /audio_arrived_before/);
});

test('only a newer generation proves cancellation; late canceled audio fails even during recovery', () => {
  const model = new PendingCancellation('thinking');
  model.status({ state: 'thinking', generation: 4 }, 10, 0);
  model.clear({ generation: 4 }, 11);
  assert.equal(model.clearAt, undefined);
  model.audio({ generation: 4 }); // The assertion specifically begins after server clear.
  model.clear({ generation: 5 }, 20);
  assert.equal(model.clearAt, 20);
  assert.throws(() => model.audio({ generation: 4 }), /canceled_generation_audio_after_clear/);
  assert.throws(() => model.audio({ generation: 3 }), /canceled_generation_audio_after_clear/);
  assert.doesNotThrow(() => model.audio({ generation: 6 }));
  model.clear({ generation: 7 }, 100);
  assert.equal(model.clearAt, 20);
});

test('arguments reject session token overrides and require values', () => {
  assert.deepEqual(options(['--pcm', 'normal.pcm', '--tool-pcm', 'tool.pcm']), { pcm: 'normal.pcm', toolPcm: 'tool.pcm' });
  assert.throws(() => options(['--token', 'secret']), /invalid_arguments/);
  assert.throws(() => options(['--tool-pcm']), /invalid_arguments/);
  assert.deepEqual(options(['--case', 'thinking']), { case: 'thinking' });
  assert.deepEqual(options(['--case', 'speaking']), { case: 'speaking' });
  assert.throws(() => options(['--case', 'unknown']), /invalid_case/);
});

test('four-input validation needs neither credentials nor network', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'real-pending-test-'));
  try {
    const normal = path.join(dir, 'normal.pcm'), tool = path.join(dir, 'tool.pcm');
    await fs.writeFile(normal, Buffer.alloc(6400, 3)); await fs.writeFile(tool, Buffer.alloc(9600, 5));
    const env = { ...process.env }; delete env.DEMO_ACCESS_KEY;
    const script = fileURLToPath(new URL('./check_real_pending.mjs', import.meta.url));
    const child = spawnSync(process.execPath, [script, '--pcm', normal, '--tool-pcm', tool, '--validate-input'], { env, encoding: 'utf8', timeout: 5000 });
    assert.equal(child.status, 0, child.stderr);
    assert.deepEqual(JSON.parse(child.stdout), { input_validated: true, network_used: false,
      planned_input_turns: 4, recovery_duration_ms: 200, tool_duration_ms: 300 });
    const single = spawnSync(process.execPath, [script, '--pcm', normal, '--tool-pcm', tool, '--case', 'thinking', '--validate-input'], { env, encoding: 'utf8', timeout: 5000 });
    assert.equal(single.status, 0, single.stderr);
    assert.equal(JSON.parse(single.stdout).planned_input_turns, 2);
    const speaking = spawnSync(process.execPath, [script, '--pcm', normal, '--case', 'speaking', '--validate-input'], { env, encoding: 'utf8', timeout: 5000 });
    assert.equal(speaking.status,0,speaking.stderr); assert.equal(JSON.parse(speaking.stdout).planned_input_turns,2);
  } finally { await fs.rm(dir, { recursive: true, force: true }); }
});

test('speaking cancellation waits for nonzero PCM and triggers exactly once',()=>{
  const model = new PendingCancellation('speaking');
  assert.equal(model.status({state:'speaking',generation:4},10,0),false);
  assert.equal(model.status({state:'thinking',generation:4},11,0),false);
  assert.equal(model.speech({generation:4},12,Buffer.alloc(960)),false);
  const pcm=Buffer.alloc(960); pcm.writeInt16LE(-1,0);
  assert.equal(model.speech({generation:4},13,pcm),true);
  assert.equal(model.generation,4); assert.equal(model.requestedAt,13);
  assert.equal(model.speech({generation:4},14,pcm),false);
  model.audio({generation:4}); // In-flight output before the server clear remains distinguishable.
  model.clear({generation:5},15);
  assert.throws(()=>model.audio({generation:4}),/canceled_generation_audio_after_clear/);
  assert.doesNotThrow(()=>model.audio({generation:5}));
  assert.equal(new PendingCancellation('thinking').speech({generation:4},12,pcm),false);
  assert.throws(()=>new PendingCancellation('speaking').speech({generation:undefined},12,pcm),/invalid_audio_generation/);
});
