// Client lifecycle tests with fake browser I/O. These do not establish audible
// voice quality, microphone permissions, provider behavior, or deployed runtime.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { PlaybackQueue, pcm16ToBase64 } from './audio-player.mjs';
const settle = () => new Promise(resolve => setImmediate(resolve));

function harness({ fetchSession, capture, transport = 'websocket', publish } = {}) {
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
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  const contexts = [], sockets = [], nodes = [], mediaRequests = [], sfus = [];
  class SfuAudioTransport {
    floor = -1; generations = []; closed = false;
    constructor(options) { this.options = options; sfus.push(this); }
    async publish() { if (publish) await publish(this); }
    async subscribe(generation) { if (generation >= this.floor) this.generations.push(generation); }
    clearOutput(generation) { this.floor = Math.max(this.floor, generation ?? this.floor+1); return 0; }
    close() { this.closed = true; }
    async resumePlayback() { this.options.onPlaybackBlocked(false); }
  }
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
    PlaybackQueue, pcm16ToBase64, SfuAudioTransport,
    document: { body: {dataset: {transport}}, getElementById: element, createElement: () => new Element() },
    window: { AudioContext, RTCPeerConnection: class {}, MediaStream: class {}, AudioWorkletNode: Node, addEventListener(type, fn) { windowListeners[type] = fn; } },
    navigator: { mediaDevices: { async getUserMedia(options) { mediaRequests.push(options); return capture ? capture(track) : { getTracks: () => [track], getAudioTracks: () => [track] }; } } },
    AudioWorkletNode: Node, WebSocket: Socket, URL, AbortSignal, Int16Array,
    location: { href: 'https://voice.example/', protocol: 'https:' },
    performance, Date, Blob, console,
    fetch: async (url, options) => {
      requests.push({url, options});
      assert.equal(url, '/api/session');
      return fetchSession ? fetchSession(url, options) : { ok: true, json: async () => ({ id: 'session-id', token: 'secret-token' }) };
    },
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay, interval: false }); return id; },
    setInterval(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay, interval: true }); return id; },
    clearTimeout(id) { timers.delete(id); }, clearInterval(id) { timers.delete(id); },
  });
  const code = fs.readFileSync(new URL('./app.js', import.meta.url), 'utf8').replace(/^import .*\n/gm, '');
  vm.runInContext(code, sandbox);
  return { element, sockets, contexts, nodes, timers, track, requests, mediaRequests, sfus, run: code => vm.runInContext(code, sandbox), windowListeners };
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
test('both transports create a session without a demo key and keep its capability', async () => {
  for (const transport of ['websocket', 'webrtc']) {
    const h = harness({transport});
    await h.element('start').click();
    assert.equal(h.requests.length, 1);
    assert.equal(h.requests[0].url, '/api/session');
    assert.deepEqual(Object.keys(h.requests[0].options.headers), ['Content-Type']);
    assert.equal(JSON.parse(h.requests[0].options.body).transport, transport);
    assert.match(h.sockets[0].url, /\?token=secret-token$/);
    h.element('end').click();
  }
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

test('microphone permission denial creates no session and releases the audio engine', async () => {
  const h = harness({capture: async () => { const error = new Error('denied'); error.name = 'NotAllowedError'; throw error; }});
  await h.element('start').click();
  assert.equal(h.requests.length, 0);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.sockets.length, 0);
  assert.match(h.element('notice').textContent, /Microphone permission was denied/);
  assert.equal(h.element('start').disabled, false);
  assert.equal(h.timers.size, 0);
});
test('End while microphone permission is pending stops a late track without creating a session', async () => {
  let finish;
  const h = harness({capture: track => new Promise(resolve => {
    finish = () => resolve({getTracks: () => [track], getAudioTracks: () => [track]});
  })});
  const starting = h.element('start').click();
  await settle();
  h.element('end').click();
  finish(); await starting;
  assert.equal(h.track.stopped, true);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.requests.length, 0);
  assert.equal(h.sockets.length, 0);
  assert.equal(h.timers.size, 0);
});
test('End during session creation retires the late capability and leaves audio stopped', async () => {
  let finish;
  const h = harness({fetchSession: () => new Promise(resolve => { finish = resolve; })});
  const starting = h.element('start').click();
  await settle();
  h.element('end').click();
  finish({ok:true, json:async()=>({id:'late-id',token:'late-token'})});
  await starting;
  assert.equal(h.track.stopped, true);
  assert.equal(h.contexts[0].state, 'closed');
  assert.equal(h.sockets.length, 1);
  assert.match(h.sockets[0].url, /late-id\?token=late-token$/);
  h.sockets[0].open();
  assert.deepEqual(h.sockets[0].sent, [{type:'end'}]);
  assert.equal(h.timers.size, 0);
});
test('audio engine resumes inside the Start click before asynchronous microphone setup', async () => {
  const h = harness();
  const starting = h.element('start').click();
  assert.equal(h.contexts.length, 1);
  assert.equal(h.contexts[0].state, 'running');
  assert.equal(h.mediaRequests.length, 0);
  await starting;
  assert.equal(h.mediaRequests.length, 1);
  assert.equal(h.requests.length, 1);
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


test('SFU mode sends microphone over WebRTC and leaves the control socket audio-free', async () => {
  const h = harness({transport:'webrtc'});
  await h.element('start').click();
  assert.equal(JSON.parse(h.requests.find(r=>r.url==='/api/session').options.body).transport, 'webrtc');
  const socket = h.sockets[0]; socket.open(); socket.receive({type:'ready',transport:'webrtc',generation:0});
  await settle();
  assert.equal(h.sfus.length,1);
  assert.equal(h.element('status').textContent,'Listening');
  assert.equal(h.run('player'),undefined);
  h.run('observeMicrophone({pcm:new Int16Array(320).fill(4000).buffer,rms:.12}); updateInputHealth()');
  assert.equal(h.element('metric-captured').textContent,'20 ms');
  assert.equal(h.element('metric-sent').textContent,'WebRTC / Opus');
  assert.equal(socket.sent.filter(p=>p.type==='audio'||p.type==='played').length,0);
  h.element('mute').click(); assert.equal(h.track.enabled,false);
  h.element('end').click(); assert.equal(h.sfus[0].closed,true); assert.equal(h.track.stopped,true);
  assert.equal(h.timers.size,0);
});
test('SFU output replaces by generation and local speech clears it without fake receipts', async () => {
  const h = harness({transport:'webrtc'}); await h.element('start').click();
  const socket=h.sockets[0]; socket.open(); socket.receive({type:'ready',generation:2});
  await settle();
  socket.receive({type:'sfu_track',generation:2});
  socket.receive({type:'status',state:'speaking',generation:2});
  for(let i=0;i<3;i++) h.run('observeMicrophone({pcm:new Int16Array(320).buffer,rms:.1})');
  assert.equal(h.sfus[0].floor,3);
  socket.receive({type:'sfu_track',generation:2});
  socket.receive({type:'sfu_track',generation:3});
  assert.deepEqual(h.sfus[0].generations,[2,3]);
  assert.equal(socket.sent.filter(p=>p.type==='interrupt').length,1);
  assert.equal(socket.sent.filter(p=>p.type==='played').length,0);
  h.element('end').click();
});
test('SFU reconnect closes peers and rejects old startup completion', async () => {
  let finish; const h=harness({transport:'webrtc',publish:()=>new Promise(resolve=>{finish=resolve;})});
  await h.element('start').click(); const first=h.sockets[0]; first.open(); first.receive({type:'ready'});
  const finishOld=finish, old=h.sfus[0]; first.close(); assert.equal(old.closed,true);
  const retry=[...h.timers].find(([,t])=>t.delay===500); h.timers.delete(retry[0]); retry[1].fn();
  const next=h.sockets[1]; next.open(); next.receive({type:'ready'});
  finishOld(); await settle();
  assert.notEqual(h.element('status').textContent,'Listening');
  finish(); await settle();
  assert.equal(h.element('status').textContent,'Listening');
  h.element('end').click(); assert.equal(h.sfus[1].closed,true);
});
test('SFU failures release all client resources and preserve the visible explanation', async () => {
  const h=harness({transport:'webrtc',publish:async()=>{throw new Error('The SFU is not configured.');}});
  await h.element('start').click(); const socket=h.sockets[0]; socket.open(); socket.receive({type:'ready'});
  await settle();
  assert.equal(h.element('notice').textContent,'The SFU is not configured.');
  assert.equal(h.track.stopped,true); assert.equal(h.sfus[0].closed,true);
  assert.equal(socket.sent.at(-1).type,'end'); assert.equal(h.timers.size,0);
});
