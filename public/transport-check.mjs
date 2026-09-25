import { SfuAudioTransport } from './sfu-client.mjs';

// Deliberately unlinked developer page. No microphone, test-only server routes,
// generated playback receipts, persisted secrets, or bundled recordings.
const $ = id => document.getElementById(`check-${id}`);
let run = 0, active = false, ready = false, ctx, destination, silence, silenceGain;
let socket, session, transport, buffer, source, sourceBegan = 0, inputDone = 0;
let heartbeat, startupTimeout, meterTimer, lastPong = 0, statsBusy = false;
let remoteStream, monitorSource, analyser, monitorGain, samples;
let generation = -1, partial = new Map();
let counts = { inputRuns: 0, rtpBytes: 0, rtpPackets: 0, windows: 0, nonzero: 0, peak: 0 };

function status(message) { $('status').textContent = message; }
function notice(message = '') { $('notice').textContent = message; $('notice').hidden = !message; }
function current(epoch) { return active && run === epoch; }
function send(message) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message)); return true;
}
function detachMonitor() {
  monitorSource?.disconnect(); analyser?.disconnect(); monitorGain?.disconnect();
  monitorSource = analyser = monitorGain = remoteStream = samples = null;
}
function observeRemote() {
  const incoming = $('audio').srcObject;
  if (incoming === remoteStream) return;
  detachMonitor();
  if (!incoming || !ctx || ctx.state === 'closed') return;
  remoteStream = incoming;
  monitorSource = ctx.createMediaStreamSource(incoming);
  analyser = ctx.createAnalyser(); analyser.fftSize = 2048;
  samples = new Float32Array(analyser.fftSize);
  monitorGain = ctx.createGain(); monitorGain.gain.value = 0;
  // Monitoring is silent. The audio element owns audible remote playback.
  monitorSource.connect(analyser).connect(monitorGain).connect(ctx.destination);
}
function inputElapsed() { return source && ctx ? Math.min(buffer.duration, Math.max(0, ctx.currentTime - sourceBegan)) : 0; }
async function measure(epoch) {
  if (!current(epoch)) return;
  $('input-ms').textContent = `${Math.round((inputDone + inputElapsed()) * 1000)} ms`;
  observeRemote();
  if (analyser && ctx?.state === 'running') {
    analyser.getFloatTimeDomainData(samples);
    let square = 0;
    for (const value of samples) square += value * value;
    const rms = Math.sqrt(square / samples.length);
    counts.windows++; if (rms > .0001) counts.nonzero++;
    counts.peak = Math.max(counts.peak, rms);
    $('sample-windows').textContent = String(counts.windows);
    $('nonzero-windows').textContent = String(counts.nonzero);
    $('peak').textContent = counts.peak.toFixed(4);
    if (counts.nonzero) $('summary').textContent = 'Nonzero audio received from the SFU. This confirms decoded remote audio, not physical speaker playback.';
  }
  const output = transport?.output;
  if (statsBusy || !output?.pc.getStats) return;
  statsBusy = true;
  try {
    const report = await output.pc.getStats();
    if (!current(epoch) || transport?.output !== output) return;
    let bytes = 0, packets = 0;
    for (const row of report.values()) if (row.type === 'inbound-rtp' && (row.kind === 'audio' || row.mediaType === 'audio')) {
      bytes += Number(row.bytesReceived) || 0; packets += Number(row.packetsReceived) || 0;
    }
    // RTP counters belong to the current generation's fresh receiving peer.
    counts.rtpBytes = bytes; counts.rtpPackets = packets;
    $('rtp-bytes').textContent = String(bytes); $('rtp-packets').textContent = String(packets);
  } catch { /* Decoded-sample observation remains available without getStats. */ }
  finally { if (current(epoch)) statsBusy = false; }
}
function transcript(packet) {
  if (!['user','assistant'].includes(packet.role) || typeof packet.text !== 'string' || !packet.text.trim()) return;
  let row = partial.get(packet.role);
  if (!row) {
    row = document.createElement('div'); row.className = `utterance ${packet.role}`;
    const label = document.createElement('div'); label.className = 'speaker'; label.textContent = packet.role === 'user' ? 'Recording transcript' : 'Agent';
    row.append(label, document.createElement('p')); $('transcript').append(row); partial.set(packet.role, row);
  }
  row.lastElementChild.textContent = packet.text.slice(0, 10000);
  if (packet.final) partial.delete(packet.role);
  while ($('transcript').children.length > 100) $('transcript').firstElementChild.remove();
  $('transcript').scrollTop = $('transcript').scrollHeight;
}
function playInput() {
  if (!active || !ready || !ctx || !buffer || source) return;
  const epoch = run, playing = ctx.createBufferSource();
  source = playing; playing.buffer = buffer; playing.connect(destination);
  sourceBegan = ctx.currentTime; counts.inputRuns++;
  $('input-runs').textContent = String(counts.inputRuns); $('replay').disabled = true;
  playing.onended = () => {
    playing.disconnect();
    if (!current(epoch) || source !== playing) return;
    inputDone += buffer.duration; source = null;
    $('input-ms').textContent = `${Math.round(inputDone * 1000)} ms`;
    $('replay').disabled = false; status('Input finished. Waiting for the agent.');
  };
  playing.start(); status('Sending prerecorded speech through WebRTC…');
}
function end(tellServer = true, message = 'Check ended. Resources released.') {
  if (tellServer) send({type:'end'});
  active = ready = false; run++;
  clearInterval(heartbeat); clearInterval(meterTimer); clearTimeout(startupTimeout);
  inputDone += inputElapsed();
  if (source) { source.onended = null; source.stop(); source.disconnect(); source = null; }
  transport?.close(); transport = null;
  detachMonitor();
  socket?.close(1000, 'Transport check ended'); socket = session = null;
  silence?.stop(); silence?.disconnect(); silenceGain?.disconnect();
  destination?.stream.getTracks().forEach(track => track.stop()); destination?.disconnect();
  ctx?.close().catch(() => {});
  ctx = destination = silence = silenceGain = buffer = null;
  statsBusy = false; partial.clear();
  $('start').disabled = $('key').disabled = $('wav').disabled = false;
  $('replay').disabled = $('interrupt').disabled = $('end').disabled = true;
  $('resume').hidden = true;
  $('input-ms').textContent = `${Math.round(inputDone * 1000)} ms`;
  status(message);
}
function fail(error, epoch) {
  if (!current(epoch)) return;
  notice(error.name === 'AbortError' ? 'The check timed out. Start it again.' : error.message || 'The transport check failed.');
  end(true, 'Check stopped.');
}
async function responseJSON(response) {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `The request failed (${response.status}).`);
  return data;
}
async function connectMedia(epoch, ws) {
  if (!current(epoch) || socket !== ws || transport) return;
  const credentials = session;
  let media;
  try {
    media = new SfuAudioTransport({
      stream: destination.stream, audio: $('audio'),
      signal: async (body, signal) => responseJSON(await fetch(`/api/session/${encodeURIComponent(credentials.id)}/sfu`, {
        method:'POST', headers:{'Content-Type':'application/json','X-Session-Token':credentials.token}, body:JSON.stringify(body), signal,
      })),
      onState: (role, state) => { if (current(epoch)) $(`${role}-state`).textContent = state; },
      onError: error => fail(error, epoch),
      onPlaybackBlocked: blocked => { if (current(epoch)) $('resume').hidden = !blocked && ctx?.state === 'running'; },
    });
    transport = media;
    status('Publishing the recording track…');
    await media.publish(); // Includes input_ready; only now may the WAV start.
    if (!current(epoch) || transport !== media || socket !== ws) { media.close(); return; }
    clearTimeout(startupTimeout); ready = true;
    $('interrupt').disabled = false; $('summary').textContent = 'Input is connected. Waiting for decoded remote audio.';
    playInput();
  } catch (error) { if (current(epoch) && transport === media) fail(error, epoch); }
}
function connectControl(epoch) {
  const url = new URL(`/api/session/${encodeURIComponent(session.id)}`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'; url.searchParams.set('token',session.token);
  const ws = new WebSocket(url); socket = ws;
  startupTimeout = setTimeout(() => fail(new Error('The SFU startup timed out.'),epoch),60000);
  ws.onopen = () => {
    if (!current(epoch) || socket !== ws) { ws.close(); return; }
    lastPong = performance.now();
    heartbeat = setInterval(() => {
      if (performance.now()-lastPong > 45000) { fail(new Error('The control connection stopped responding.'),epoch); return; }
      send({type:'ping'});
    },3000);
  };
  ws.onmessage = event => {
    if (!current(epoch) || socket !== ws) return;
    try {
      if (typeof event.data !== 'string' || event.data.length > 2_100_000) throw new Error('Invalid control message.');
      const packet = JSON.parse(event.data);
      if (Number.isSafeInteger(packet.generation)) { generation = Math.max(generation,packet.generation); $('generation').textContent = `Generation ${generation}`; }
      if (packet.type === 'ready') connectMedia(epoch,ws);
      else if (packet.type === 'sfu_track' && transport) {
        const media = transport;
        if (!Number.isSafeInteger(packet.generation) || packet.generation < media.floor || media.output?.generation === packet.generation) return;
        counts.windows=counts.nonzero=counts.peak=0;
        $('sample-windows').textContent=$('nonzero-windows').textContent='0'; $('peak').textContent='0.0000';
        $('summary').textContent='Waiting for decoded audio in this response generation.';
        $('rtp-bytes').textContent = $('rtp-packets').textContent = '0';
        media.subscribe(packet.generation).catch(error => { if (current(epoch) && media === transport && error.name !== 'AbortError') fail(error,epoch); });
      } else if (packet.type === 'clear') { transport?.clearOutput(packet.generation); detachMonitor(); }
      else if (packet.type === 'transcript') transcript(packet);
      else if (packet.type === 'status' && !source) status(String(packet.state || 'Listening'));
      else if (packet.type === 'pong') {
        lastPong = performance.now();
        for (const [field,id] of [['input_audio_bytes','server-ms'],['forwarded_audio_bytes','provider-ms']]) if (Number.isSafeInteger(packet.audio?.[field])) $(id).textContent = `${Math.round(packet.audio[field]/32)} ms`;
      } else if (packet.type === 'error') fail(new Error(packet.message || 'The server reported an error.'),epoch);
      else if (packet.type === 'ended') end(false);
      // There is intentionally no 'audio' or 'played' control path here.
    } catch (error) { fail(error,epoch); }
  };
  ws.onerror = () => {};
  ws.onclose = () => { if (current(epoch) && socket === ws) end(false,'Control connection closed. Check ended.'); };
}
async function start() {
  if (active) return;
  const keyFile = $('key').files?.[0], wav = $('wav').files?.[0];
  if (!keyFile || !wav) { notice('Choose both the key text file and a speech WAV.'); return; }
  const epoch = ++run; active = true; ready = false; generation = -1; inputDone = 0;
  counts = { inputRuns:0,rtpBytes:0,rtpPackets:0,windows:0,nonzero:0,peak:0 };
  $('start').disabled = $('key').disabled = $('wav').disabled = true; $('end').disabled = false;
  $('transcript').replaceChildren(); partial.clear(); notice();
  for (const id of ['input-ms','server-ms','provider-ms']) $(id).textContent='0 ms';
  for (const id of ['input-runs','rtp-bytes','rtp-packets','sample-windows','nonzero-windows']) $(id).textContent='0';
  $('generation').textContent='Generation —'; $('peak').textContent='0.0000';
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass || !window.RTCPeerConnection) throw new Error('This browser needs Web Audio and WebRTC support.');
    ctx = new AudioContextClass({latencyHint:'interactive'});
    const audioContext = ctx;
    audioContext.resume().catch(() => {}); // Preserve the Start click gesture.
    ctx.onstatechange = () => { if (current(epoch)) $('resume').hidden = ctx.state === 'running'; };
    $('resume').hidden = ctx.state === 'running';
    status('Reading files and checking access…');
    if (keyFile.size > 4096 || wav.size > 20*1024*1024) throw new Error('Use a small key file and a WAV under 20 MB.');
    const key = (await keyFile.text()).trim();
    if (!current(epoch)) return;
    if (!key || /[\r\n\x00-\x1f\x7f]/.test(key)) throw new Error('The key file must contain one access key.');
    const headers = {'Content-Type':'application/json','X-Demo-Key':key};
    await responseJSON(await fetch('/api/access',{method:'POST',headers,signal:AbortSignal.timeout(15000)}));
    if (!current(epoch)) return;
    const bytes = await wav.arrayBuffer();
    if (!current(epoch)) return;
    const header = new Uint8Array(bytes,0,Math.min(12,bytes.byteLength));
    if (String.fromCharCode(...header.slice(0,4)) !== 'RIFF' || String.fromCharCode(...header.slice(8,12)) !== 'WAVE') throw new Error('Choose a RIFF/WAVE audio recording.');
    const decoded = await audioContext.decodeAudioData(bytes);
    if (!current(epoch)) return;
    buffer = decoded;
    if (buffer.duration <= 0 || buffer.duration > 60) throw new Error('Choose a speech recording between 0 and 60 seconds long.');
    $('duration').textContent=`${buffer.duration.toFixed(2)} s`;
    destination = audioContext.createMediaStreamDestination();
    silence = audioContext.createOscillator(); silenceGain = audioContext.createGain(); silenceGain.gain.value=0;
    silence.connect(silenceGain).connect(destination); silence.start();
    await audioContext.resume();
    if (!current(epoch)) return;
    status('Creating a normal WebRTC conversation…');
    const created = await responseJSON(await fetch('/api/session',{method:'POST',headers,body:JSON.stringify({transport:'webrtc'}),signal:AbortSignal.timeout(20000)}));
    if (typeof created.id !== 'string' || typeof created.token !== 'string') throw new Error('The server returned an invalid conversation.');
    if (!current(epoch)) {
      // Creation completed after End: send End to the returned capability so
      // it cannot leave a provider session running unnoticed.
      const url=new URL(`/api/session/${encodeURIComponent(created.id)}`,location.href);
      url.protocol=location.protocol==='https:'?'wss:':'ws:'; url.searchParams.set('token',created.token);
      const abandoned=new WebSocket(url), timeout=setTimeout(()=>abandoned.close(),5000);
      abandoned.onopen=()=>{abandoned.send(JSON.stringify({type:'end'}));abandoned.close(1000,'Ended during startup');clearTimeout(timeout);};
      abandoned.onclose=()=>clearTimeout(timeout);
      return;
    }
    session=created; meterTimer=setInterval(()=>measure(epoch).catch(error=>fail(error,epoch)),100); connectControl(epoch);
  } catch (error) { fail(error,epoch); }
}
$('start').addEventListener('click',start);
$('end').addEventListener('click',()=>end());
$('replay').addEventListener('click',playInput);
$('interrupt').addEventListener('click',()=>{
  if (!active) return;
  transport?.clearOutput(Math.max(generation,transport?.floor ?? -1)+1); detachMonitor(); send({type:'interrupt'});
  status('Interrupted. Replay input to test the next response.');
});
$('resume').addEventListener('click',async()=>{
  if (!active || !ctx) return;
  const epoch=run;
  try { const playback=transport?.resumePlayback(); await ctx.resume(); await playback; if(current(epoch)) $('resume').hidden=true; }
  catch(error){fail(error,epoch);}
});
window.addEventListener('pagehide',()=>{if(active) end();});
