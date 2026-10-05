import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

test('real-provider harnesses request sessions without a shared key', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'keyless-session-test-'));
  try {
    const pcm = path.join(dir, 'input.pcm');
    const preload = path.join(dir, 'fetch-fixture.mjs');
    await fs.writeFile(pcm, Buffer.alloc(6400, 3));
    // A deliberate HTTP failure stops each harness before model or socket use.
    // Every fetch is intercepted; this test never contacts a deployed Worker.
    await fs.writeFile(preload, `
      import { appendFileSync } from 'node:fs';
      globalThis.fetch = async (url, options = {}) => {
        appendFileSync(process.env.KEYLESS_REQUEST_LOG, JSON.stringify({
          url: String(url), method: options.method,
          headers: Object.fromEntries(new Headers(options.headers)),
        }) + '\\n');
        return { ok: false, status: 503 };
      };
    `);
    for (const name of ['check_real_voice', 'check_real_pending', 'smoke_real_voice', 'check_real_idle']) {
      const requestLog = path.join(dir, `${name}-requests.jsonl`);
      const evidence = path.join(dir, `${name}-evidence.json`);
      const script = fileURLToPath(new URL(`./${name}.mjs`, import.meta.url));
      const env = { ...process.env, KEYLESS_REQUEST_LOG: requestLog };
      delete env.DEMO_ACCESS_KEY;
      const args = name === 'check_real_idle'
        ? ['https://voice.example', evidence]
        : ['--base', 'https://voice.example', '--pcm', pcm, '--capture-root', dir, '--evidence', evidence,
          ...(name === 'check_real_pending' ? ['--tool-pcm', pcm, '--case', 'thinking'] : [])];
      const child = spawnSync(process.execPath, ['--import', preload, script, ...args], {
        env, encoding: 'utf8', timeout: 5000,
      });
      assert.equal(child.status, 1, `${name}: expected the synthetic HTTP failure\n${child.stderr}`);
      const requests = (await fs.readFile(requestLog, 'utf8')).trim().split('\n').map(line => JSON.parse(line));
      assert.deepEqual(requests, [{ url: 'https://voice.example/api/session', method: 'POST', headers: {} }], name);
      assert.equal(JSON.parse(await fs.readFile(evidence, 'utf8')).passed, false, name);
    }
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
