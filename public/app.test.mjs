// Client lifecycle tests with fake browser I/O. These do not establish audible
// voice quality, microphone permissions, provider behavior, or deployed runtime.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { PlaybackQueue, pcm16ToBase64 } from './audio-player.mjs';

function harness({ fetchSession, fetchAccess } = {}) {
  class Element {
    children = []; listeners = {}; hidden = false; disabled = false;
    classList = { toggle() {} }; attributes = {}; textContent = '';
    scrollHeight = 0; scrollTop = 0; clientHeight = 500;
    append(...children) { for (const child of children) child.parent = this; this.children.push(...children); }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    get lastElementChild() { return this.children.at(-1); }
    addEventListener(type, fn) { this.listeners[type] = fn; }
    setAttribute(name, value) { this.attributes[name] = value; }
    remove() { this.parent.children = this.parent.children.filter(child => child !== this); }
    click() { return this.listeners.click?.(); }
    focus() { this.focused = true; }
    select() { this.selected = true; }
  }
  const elements = new Map();
  const element = id => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  element('transcript').append(element('empty-state'));
  element('access-key').value = 'demo-key';
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const contexts = [], sockets = [], nodes = [], mediaRequests = [];
  const track = { enabled: true, stopped: false, addEventListener() {}, stop() { this.stopped = true; } };
  class Node {
    gain = {}; port = { close() { this.closed = true; } };
    constructor() { nodes.push(this); }
    connect(next) { return next; }
    disconnect() { this.disconnected = true; }
  }
  class AudioContext {
    state = 'running'; sampleRate = 48000; currentTime = 0; destination = {};
    audioWorklet = { async addModule() {} };
    constructor() { contexts.push(this); }
    async resume() { this.state = 'running'; }
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return new Node(); }
    createGain() { return new Node(); }
    createBuffer(channels, length, sampleRate) { return { length, sampleRate, copyToChannel() {} }; }
    createBufferSource() { const node = new Node(); node.start = () => {}; node.stop = () => { node.stopped = true; }; return node; }
  }
  class Socket {
    static OPEN = 1;
    readyState = 0; bufferedAmount = 0; sent = [];
    constructor(url) { this.url = url.toString(); sockets.push(this); }
    send(message) { this.sent.push(JSON.parse(message)); }
    open() { this.readyState = 1; this.onopen?.(); }
    receive(packet) { this.onmessage?.({ data: JSON.stringify(packet) }); }
    close(code = 1006, reason = '') { this.readyState = 3; this.onclose?.({ code, reason }); }
  }
  const windowListeners = {};
  const sandbox = vm.createContext({
    PlaybackQueue, pcm16ToBase64,
    document: { getElementById: element, createElement: () => new Element() },
    window: { AudioContext, AudioWorkletNode: Node, addEventListener(type, fn) { windowListeners[type] = fn; } },
    navigator: { mediaDevices: { async getUserMedia(options) { mediaRequests.push(options); return { getTracks: () => [track], getAudioTracks: () => [track] }; } } },
    AudioWorkletNode: Node, WebSocket: Socket, URL, AbortSignal, Int16Array,
    location: { href: 'https://voice.example/', protocol: 'https:' },
    performance, Date, Blob, console,
    fetch: async (url, options) => {
      requests.push({url, options});
      if (url === '/api/access') return fetchAccess ? fetchAccess(url, options) : {ok:true, json:async()=>({ok:true})};
      return fetchSession ? fetchSession(url, options) : { ok: true, json: async () => ({ id: 'session-id', token: 'secret-token' }) };
    },
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay, interval: false }); return id; },
    setInterval(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay, interval: true }); return id; },
    clearTimeout(id) { timers.delete(id); }, clearInterval(id) { timers.delete(id); },
  });
  const code = fs.readFileSync(new URL('./app.js', import.meta.url), 'utf8').replace(/^import .*\n/, '');
  vm.runInContext(code, sandbox);
  return { element, sockets, contexts, nodes, timers, track, requests, mediaRequests, run: code => vm.runInContext(code, sandbox), windowListeners };
}

test('Start, ready, mute, and End release all client resources', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  assert.match(socket.url, /wss:\/\/voice.example\/api\/session\/session-id\?token=secret-token/);
  socket.open(); socket.receive({ type: 'ready' });
  assert.equal(h.element('status').textContent, 'Listening');
  assert.equal(h.element('mute').disabled, false);
  h.element('mute').click();
  assert.equal(h.track.enabled, false);
  assert.equal(h.element('mute').attributes['aria-pressed'], 'true');
  h.element('end').click();
  assert.equal(socket.sent.at(-1).type, 'end');
  assert.equal(h.track.stopped, true);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.timers.size, 0);
  assert.equal(h.element('start').disabled, false);
  assert.ok(h.nodes.every(node => node.disconnected));
});
test('disconnect clears playback and reconnect reuses capability with a bounded retry budget', async () => {
  const h = harness();
  await h.element('start').click();
  let socket = h.sockets[0];
  socket.open(); socket.receive({ type: 'ready' });
  socket.receive({ type: 'audio', generation: 5, chunk_id: 1, sample_rate: 16000, data: pcm16ToBase64(new Int16Array(1600).buffer) });
  assert.equal(h.run('player.hasPending'), true);
  for (let attempt = 0; attempt < 4; attempt++) {
    socket.close();
    assert.equal(h.run('player.hasPending'), false);
    const timer = [...h.timers].find(([, timer]) => !timer.interval && timer.delay === Math.min(4000, 500 * 2 ** attempt));
    assert.ok(timer);
    h.timers.delete(timer[0]); timer[1].fn();
    socket = h.sockets.at(-1);
    assert.equal(socket.url, h.sockets[0].url);
    socket.open(); socket.receive({ type: 'ready' });
  }
  socket.close();
  assert.equal(h.sockets.length, 5);
  assert.equal(h.run('active'), false);
  assert.equal(h.element('status').textContent, 'Disconnected');
});
test('reset creates a new stream epoch and stale socket messages are ignored', async () => {
  const h = harness();
  await h.element('start').click();
  const old = h.sockets[0];
  old.open(); old.receive({ type: 'ready' }); old.close();
  const timer = [...h.timers].find(([, timer]) => timer.delay === 500);
  h.timers.delete(timer[0]); timer[1].fn();
  const current = h.sockets[1];
  current.open(); current.receive({ type: 'reset', generation: 0 }); current.receive({ type: 'ready' });
  const packet = { type: 'audio', generation: 0, chunk_id: 1, sample_rate: 16000, data: pcm16ToBase64(new Int16Array(1600).buffer) };
  old.receive(packet);
  assert.equal(h.run('player.hasPending'), false);
  current.receive(packet);
  assert.equal(h.run('player.hasPending'), true);
  h.element('end').click();
});
test('failed session creation closes microphone and shows a recoverable error', async () => {
  const h = harness({ fetchSession: async () => ({ ok: false, status: 503, json: async () => ({ error: 'Provider is not configured.' }) }) });
  await h.element('start').click();
  assert.equal(h.element('notice').textContent, 'Provider is not configured.');
  assert.equal(h.track.stopped, true);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.element('start').disabled, false);
});
test('access key stays in authentication headers and out of capability URLs', async () => {
  let request;
  const h = harness({ fetchSession: async (url, options) => { request = options; return { ok: true, json: async () => ({ id: 'id', token: 'capability' }) }; } });
  h.element('access-key').value = 'demo-key';
  await h.element('start').click();
  assert.equal(request.headers['X-Demo-Key'], 'demo-key');
  assert.doesNotMatch(h.sockets[0].url, /demo-key/);
  h.element('end').click();
});
test('speech onset during pending generation rejects its delayed first audio', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  socket.open(); socket.receive({ type: 'ready', generation: 4 });
  socket.receive({ type: 'status', state: 'thinking', generation: 4 });
  for (let frame = 0; frame < 3; frame++) h.run('observeMicrophone({pcm:new Int16Array(320).buffer,rms:.1})');
  assert.equal(socket.sent.filter(packet => packet.type === 'interrupt').length, 1);
  socket.receive({ type: 'audio', generation: 4, chunk_id: 1, sample_rate: 16000, data: pcm16ToBase64(new Int16Array(1600).buffer) });
  assert.equal(h.run('player.hasPending'), false);
  socket.receive({ type: 'audio', generation: 5, chunk_id: 2, sample_rate: 16000, data: pcm16ToBase64(new Int16Array(1600).buffer) });
  assert.equal(h.run('player.hasPending'), true);
  h.element('end').click();
});
test('reset replaces visible history and excludes restored turns from response metrics', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  socket.open(); socket.receive({ type: 'ready', generation: 1 });
  socket.receive({ type: 'transcript', role: 'user', text: 'My name is Sam.', final: true });
  socket.receive({ type: 'transcript', role: 'assistant', text: 'Words that never finished playing.', final: true });
  const history = [{ role: 'user', content: 'My name is Sam.' }, { role: 'assistant', content: 'Hello Sam.' }];
  socket.receive({ type: 'reset', generation: 2, history });
  socket.receive({ type: 'reset', generation: 3, history });
  const entries = h.element('transcript').children.map(node => node.lastElementChild?.textContent).filter(Boolean);
  assert.deepEqual(entries, ['My name is Sam.', 'Hello Sam.']);
  assert.equal(h.run('metrics.events.filter(event => event.event === "user_transcript_final").length'), 1);
  h.element('end').click();
});
test('terminal server close releases microphone without futile reconnect', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  socket.open(); socket.receive({ type: 'ready' });
  socket.receive({ type: 'error', message: 'Speech recognition stopped; start a new call.' });
  socket.close(1000, 'terminal_provider_failure');
  assert.equal(h.track.stopped, true);
  assert.equal(h.run('active'), false);
  assert.equal(h.element('notice').textContent, 'Speech recognition stopped; start a new call.');
  assert.equal(h.run('metrics.reconnects'), 0);
  assert.equal(h.timers.size, 0);
});
test('server session time limit is explained and releases resources', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  socket.open(); socket.receive({ type: 'ready' });
  socket.close(1000, 'fifteen_minute_limit');
  assert.match(h.element('notice').textContent, /15-minute call limit/);
  assert.equal(h.track.stopped, true);
  assert.equal(h.run('metrics.reconnects'), 0);
});

// Provider startup capacity failures must not multiply the server retry budget.
test('terminal startup error preserves explanation and releases microphone without reconnect', async () => {
  const h = harness();
  await h.element('start').click();
  const socket = h.sockets[0];
  socket.open();
  socket.receive({ type: 'error', message: 'Speech service is busy. Please try again shortly.', recoverable: false });
  socket.close(1012, 'old transport close');
  assert.equal(h.element('notice').textContent, 'Speech service is busy. Please try again shortly.');
  assert.equal(h.element('status').textContent, 'Could not connect');
  assert.equal(h.track.stopped, true);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.run('metrics.reconnects'), 0);
  assert.equal(h.timers.size, 0);
  assert.equal(h.element('start').disabled, false);
});

test('rejected key does not open the microphone or create a session', async () => {
  const h = harness({ fetchAccess: async () => ({ok:false, status:401, json:async()=>({error:'That access key was not accepted.'})}) });
  await h.element('start').click();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/access');
  assert.equal(h.contexts.length, 1);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.mediaRequests.length, 0);
  assert.equal(h.sockets.length, 0);
  assert.equal(h.element('access-key').focused, true);
  assert.equal(h.element('access-key').selected, true);
  assert.equal(h.element('notice').textContent, 'That access key was not accepted.');
});
test('key is trimmed and captured before asynchronous permission setup', async () => {
  let h;
  h = harness({fetchAccess: async () => {
    h.element('access-key').value = 'changed-during-setup';
    return {ok:true, json:async()=>({ok:true})};
  }});
  h.element('access-key').value = '  Original_Key-42  \n';
  await h.element('start').click();
  assert.equal(h.requests.length, 2);
  assert.ok(h.requests.every(r => r.options.headers['X-Demo-Key'] === 'Original_Key-42'));
  h.element('end').click();
});
test('loading the key file verifies access without microphone, session, or persistent storage', async () => {
  const h = harness();
  h.element('key-file').files = [{size:44, text:async()=> 'file-key\n'}];
  await h.element('key-file').listeners.change();
  assert.equal(h.element('access-key').value, 'file-key');
  assert.match(h.element('key-status').textContent, /Access key verified/);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].options.headers['X-Demo-Key'], 'file-key');
  assert.equal(h.contexts.length, 0);
  assert.equal(h.sockets.length, 0);
  assert.equal(h.element('start').disabled, false);
});
test('oversized or multiline key files are rejected before any request', async () => {
  for (const file of [{size:5000, text:async()=> 'too big'}, {size:20,text:async()=> 'key\nsecond-line'}]) {
    const h = harness(); h.element('key-file').files = [file];
    await h.element('key-file').listeners.change();
    assert.equal(h.requests.length, 0);
    assert.equal(h.contexts.length, 0);
    assert.notEqual(h.element('notice').textContent, '');
  }
});

test('audio engine is prepared before the authentication await, microphone after it', async () => {
  let h;
  h = harness({fetchAccess: async () => {
    assert.equal(h.contexts.length, 1);
    assert.equal(h.contexts[0].state, 'running');
    assert.equal(h.mediaRequests.length, 0);
    return {ok:true, json:async()=>({ok:true})};
  }});
  await h.element('start').click();
  assert.equal(h.mediaRequests.length, 1);
  h.element('end').click();
});
test('capture, upload and server delivery counters distinguish each stage without audio capture', async () => {
  const h = harness(); await h.element('start').click();
  const socket = h.sockets[0]; socket.open(); socket.receive({type:'ready'});
  h.run('observeMicrophone({pcm:new Int16Array(320).fill(4000).buffer,rms:.12}); updateInputHealth()');
  assert.equal(h.element('metric-captured').textContent, '20 ms');
  assert.equal(h.element('metric-sent').textContent, '20 ms');
  assert.equal(h.element('metric-received').textContent, '0 ms');
  assert.match(h.element('mic-signal').textContent, /picking up sound/);
  socket.receive({type:'pong', audio:{input_audio_bytes:640,forwarded_audio_bytes:640}});
  assert.equal(h.element('metric-received').textContent, '20 ms');
  assert.equal(h.element('metric-forwarded').textContent, '20 ms');
  assert.ok(h.element('mic-level').value > 0);
  h.element('end').click();
});
test('paused audio exposes resume action and fatal worklet errors stop the call', async () => {
  const h = harness(); await h.element('start').click();
  h.contexts[0].state = 'suspended'; h.run('updateInputHealth()');
  assert.equal(h.element('resume-audio').hidden, false);
  assert.match(h.element('mic-signal').textContent, /Audio is paused/);
  await h.element('resume-audio').click();
  assert.equal(h.contexts[0].state, 'running');
  assert.equal(h.element('resume-audio').hidden, true);
  h.run('capture.onprocessorerror()');
  assert.equal(h.track.stopped, true);
  assert.equal(h.element('notice').textContent, 'Microphone processing stopped. Start a new conversation.');
  assert.equal(h.timers.size, 0);
});
test('resume remains reachable while initial audio startup is waiting', async () => {
  const h = harness();
  h.run(`let resumeWaiters = []; window.AudioContext.prototype.resume = function() {
    this.state = 'suspended'; return new Promise(resolve => resumeWaiters.push(resolve));
  };`);
  const starting = h.element('start').click();
  assert.equal(h.element('resume-audio').hidden, false);
  assert.ok([...h.timers.values()].some(timer => timer.interval && timer.delay === 250));
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.mediaRequests.length, 0);
  h.run(`window.AudioContext.prototype.resume = async function() {
    this.state = 'running'; resumeWaiters.splice(0).forEach(resolve => resolve());
  };`);
  await h.element('resume-audio').click();
  await starting;
  assert.equal(h.mediaRequests.length, 1);
  h.element('end').click();
  assert.equal(h.timers.size, 0);
});
