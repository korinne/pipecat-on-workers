import { PlaybackQueue, pcm16ToBase64 } from './audio-player.mjs';
import { SfuAudioTransport } from './sfu-client.mjs';
import { shareableServerDiagnostics } from './session-measurements.mjs';

const transport = document.body?.dataset.transport === 'webrtc' ? 'webrtc' : 'websocket';
const usesSfu = transport === 'webrtc';

const $ = id => document.getElementById(id);
const ui = Object.fromEntries(['start', 'mute', 'end', 'status', 'status-dot', 'transcript', 'empty-state', 'notice', 'input-hint', 'session-time'].map(id => [id, $(id)]));
const metrics = { transport, startupMs: [], firstAudioAfterTranscriptMs: [], localPlaybackClearMs: [], reconnects: 0, errors: [], events: [] };
const MAX_METRICS = 1000;
let context, stream, capture, inputNode, silentGain, player, socket, session, sfu;
let remotePlaybackBlocked = false;
let active = false, muted = false, serverReady = false, starting = false, lifecycle = 0;
let startedAt = 0, requestedAt = 0, finalTranscriptAt = null, connectAttempt = 0;
let reconnectTimer, heartbeatTimer, elapsedTimer, lastPong = 0;
let loudFrames = 0, quietFrames = 0, speechActive = false, lastInterrupt = -Infinity;
let serverState = '', partial = new Map();
let knownGeneration = -1;
let inputHealthTimer, lastCaptureAt = 0, lastSoundAt = 0, lastRms = 0;
let audioStats = { capturedChunks: 0, capturedBytes: 0, sentChunks: 0, sentBytes: 0, maxRms: 0, receivedBytes: 0, forwardedBytes: 0 };
let lastAudioClock = 0, lastClockAdvanceAt = 0;
function updateInputHealth() {
  const now = performance.now(), track = stream?.getAudioTracks()[0];
  const state = context?.state || 'closed';
  if (context && context.currentTime !== lastAudioClock) { lastAudioClock = context.currentTime; lastClockAdvanceAt = now; }
  const clockStalled = Boolean(context && stream && now-lastClockAdvanceAt > 2500);
  $('metric-audio-state').textContent = `${state}${context ? ' · '+context.currentTime.toFixed(1)+' s' : ''}`;
  $('metric-input-track').textContent = track ? `${track.readyState || 'live'}${track.muted ? ' · paused' : ''}` : '—';
  $('metric-captured').textContent = `${Math.round(audioStats.capturedBytes / 32)} ms`;
  $('metric-sent').textContent = usesSfu ? 'WebRTC / Opus' : `${Math.round(audioStats.sentBytes / 32)} ms`;
  $('metric-received').textContent = `${Math.round(audioStats.receivedBytes / 32)} ms`;
  $('metric-forwarded').textContent = `${Math.round(audioStats.forwardedBytes / 32)} ms`;
  $('metric-input-peak').textContent = audioStats.maxRms.toFixed(4);
  $('mic-level').value = active && !muted && now-lastCaptureAt < 500 ? Math.min(1, lastRms * 12) : 0;
  $('resume-audio').hidden = !active || !context || (state === 'running' && !clockStalled && !remotePlaybackBlocked);
  let message = 'Microphone not started';
  if (active && context) {
    if (remotePlaybackBlocked) message = 'Tap Resume audio to hear the agent.';
    else if (muted) message = 'Microphone muted';
    else if (state !== 'running' || clockStalled) message = 'Audio is paused. Tap Resume audio.';
    else if (!stream) message = 'Waiting for microphone access…';
    else if (track?.muted) message = 'Your browser has paused the microphone.';
    else if (now-lastCaptureAt > 2500) message = 'No microphone samples are arriving. Check your input device.';
    else if (now-lastSoundAt < 1500) message = 'Microphone is picking up sound locally.';
    else message = 'No sound detected yet. Speak to begin.';
  }
  $('mic-signal').textContent = message;
  metrics.audioInput = { ...audioStats, contextState: state, contextTime: context?.currentTime || 0, trackState: track?.readyState || null, trackMuted: Boolean(track?.muted) };
}
function prepareAudioContext() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) throw new Error('This browser does not support voice playback.');
  context = new AudioContextClass({ latencyHint: 'interactive' });
  // Run resume synchronously inside the Start click, before asynchronous setup.
  context.resume().catch(() => {});
  context.onstatechange = updateInputHealth;
  lastAudioClock = context.currentTime; lastClockAdvanceAt = performance.now();
}

function event(name, extra = {}) {
  metrics.events.push({ event: name, elapsedMs: Math.round(performance.now() - requestedAt), ...extra });
  if (metrics.events.length > MAX_METRICS) metrics.events.shift();
}
function record(name, value, display) {
  metrics[name].push(Math.round(value * 10) / 10);
  if (metrics[name].length > MAX_METRICS) metrics[name].shift();
  $(display).textContent = `${Math.round(value)} ms`;
}
function status(text, kind = '') {
  ui.status.textContent = text;
  ui['status-dot'].className = `status-dot ${kind}`;
}
function resetResponseState() {
  serverState = 'listening';
  if (serverReady) status(muted ? 'Microphone muted' : 'Listening', 'live');
}
function notice(message) {
  ui.notice.textContent = message;
  ui.notice.hidden = !message;
  if (message) { metrics.errors.push({ at: new Date().toISOString(), message }); if (metrics.errors.length > MAX_METRICS) metrics.errors.shift(); }
}
function send(message) {
  if (socket?.readyState !== WebSocket.OPEN) return false;
  if (socket.bufferedAmount > 256 * 1024) {
    socket.close(4001, 'Upload backpressure');
    return false;
  }
  socket.send(JSON.stringify(message));
  return true;
}
function scrollIfNearBottom(action) {
  const stick = ui.transcript.scrollHeight - ui.transcript.scrollTop - ui.transcript.clientHeight < 90;
  action();
  if (stick) ui.transcript.scrollTop = ui.transcript.scrollHeight;
}
function transcript({ role, text, final }, { restored = false } = {}) {
  if (typeof text !== 'string' || !text.trim()) return;
  role = role === 'user' ? 'user' : 'assistant';
  scrollIfNearBottom(() => {
    ui['empty-state'].hidden = true;
    let entry = partial.get(role);
    if (!entry) {
      entry = document.createElement('div');
      entry.className = `utterance ${role}`;
      const label = document.createElement('div');
      label.className = 'speaker';
      label.textContent = role === 'user' ? 'You' : 'Agent';
      entry.append(label, document.createElement('p'));
      ui.transcript.append(entry);
      partial.set(role, entry);
    }
    entry.lastElementChild.textContent = text;
    entry.classList.toggle('partial', !final);
    if (final) partial.delete(role);
    while (ui.transcript.children.length > 301) ui.transcript.children[1].remove();
  });
  if (role === 'user' && final && !restored) { finalTranscriptAt = performance.now(); event('user_transcript_final'); }
}
function systemMessage(text) {
  scrollIfNearBottom(() => {
    ui['empty-state'].hidden = true;
    const entry = document.createElement('p');
    entry.className = 'system-message';
    entry.textContent = text;
    ui.transcript.append(entry);
  });
}
function clearPlayback(generation, reason) {
  if (usesSfu) {
    if (!sfu) return;
    if (generation === undefined) generation = Math.max(knownGeneration, sfu.floor) + 1;
    record('localPlaybackClearMs', sfu.clearOutput(generation), 'metric-clear');
    event('speaker_detached', { reason, generation });
    return;
  }
  if (!player) return;
  if (generation === undefined) generation = Math.max(knownGeneration, player.generation) + 1;
  record('localPlaybackClearMs', player.clear(generation), 'metric-clear');
  event('playback_clear', { reason, generation });
}
function interrupt(reason) {
  const now = performance.now();
  if (now - lastInterrupt < 350) return;
  lastInterrupt = now;
  clearPlayback(undefined, reason);
  send({ type: 'interrupt' });
  event('interrupt_sent', { reason });
}
function observeMicrophone({ pcm, rms }) {
  if (!active) return;
  if (!pcm || !Number.isFinite(rms)) return;
  lastCaptureAt = performance.now(); lastRms = Math.max(0, rms);
  if (rms > .003 && !muted) lastSoundAt = lastCaptureAt;
  audioStats.capturedChunks++; audioStats.capturedBytes += pcm.byteLength;
  audioStats.maxRms = Math.max(audioStats.maxRms, rms);
  if (!serverReady) return;
  if (muted) { loudFrames = quietFrames = 0; speechActive = false; }
  else if (rms > .018) {
    loudFrames++;
    quietFrames = 0;
    if (!speechActive && loudFrames >= 3) {
      speechActive = true;
      if (player?.hasPending || ['thinking', 'generating', 'responding', 'speaking', 'tool'].includes(serverState)) interrupt('microphone_speech_onset');
    }
  } else {
    loudFrames = 0;
    if (++quietFrames >= 12) speechActive = false;
  }
  if (usesSfu) return; // WebRTC publishes the microphone track; no PCM on the control socket.
  // Muting emits silence so provider turn detection can finish an utterance.
  if (muted) new Int16Array(pcm).fill(0);
  if (send({ type: 'audio', data: pcm16ToBase64(pcm), sample_rate: 16000 })) { audioStats.sentChunks++; audioStats.sentBytes += pcm.byteLength; }
}

async function setupAudio(run) {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) throw new Error('This browser needs microphone and AudioWorklet support. Open the demo over HTTPS or localhost.');
  if (!context) prepareAudioContext();
  const thisContext = context;
  await thisContext.resume();
  if (run !== lifecycle) return;
  const captured = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  if (run !== lifecycle) { captured.getTracks().forEach(track => track.stop()); return; }
  stream = captured;
  await thisContext.audioWorklet.addModule('/capture-processor.js');
  if (run !== lifecycle) return;
  inputNode = thisContext.createMediaStreamSource(stream);
  capture = new AudioWorkletNode(thisContext, 'pcm16-capture', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
  silentGain = thisContext.createGain();
  silentGain.gain.value = 0;
  inputNode.connect(capture).connect(silentGain).connect(thisContext.destination);
  const thisCapture = capture;
  capture.port.onmessage = e => { if (run === lifecycle && capture === thisCapture) observeMicrophone(e.data); };
  capture.onprocessorerror = () => { if (run === lifecycle) { notice('Microphone processing stopped. Start a new conversation.'); endSession(); } };
  lastCaptureAt = lastClockAdvanceAt = performance.now();
  updateInputHealth();
  $('metric-samplerate').textContent = usesSfu ? `${thisContext.sampleRate.toLocaleString()} Hz · WebRTC` : `${thisContext.sampleRate.toLocaleString()} → 16,000 Hz`;
  if (!usesSfu) player = new PlaybackQueue(thisContext, {
    onPlayed: packet => { send({ type: 'played', ...packet }); event('chunk_played', packet); },
    onChange: seconds => { $('metric-queue').textContent = `${Math.round(seconds * 1000)} ms`; },
    onFirstAudio: ({ generation, chunk_id, scheduledAt }) => {
      const untilScheduledStart = (scheduledAt - thisContext.currentTime) * 1000;
      if (finalTranscriptAt !== null) {
        record('firstAudioAfterTranscriptMs', performance.now() - finalTranscriptAt + untilScheduledStart, 'metric-response');
        finalTranscriptAt = null;
      }
      event('audio_scheduled', { generation, chunk_id, untilScheduledStartMs: Math.round(untilScheduledStart) });
    },
  });
  for (const track of stream.getAudioTracks()) track.addEventListener('ended', () => {
    if (active && run === lifecycle) { notice('Microphone access ended. Start again to reconnect.'); endSession(); }
  });
}

async function start() {
  if (active || starting) return;
  const run = ++lifecycle;
  active = starting = true;
  for (const key of ['startupMs', 'firstAudioAfterTranscriptMs', 'localPlaybackClearMs', 'errors', 'events']) metrics[key] = [];
  metrics.reconnects = 0;
  delete metrics.audioInput;
  muted = false;
  serverReady = false;
  session = null;
  connectAttempt = 0;
  knownGeneration = -1;
  requestedAt = performance.now();
  startedAt = 0;
  finalTranscriptAt = null;
  partial.clear();
  ui.transcript.replaceChildren(ui['empty-state']);
  ui['empty-state'].hidden = false;
  ui['session-time'].textContent = '00:00';
  ui.start.disabled = true;
  ui.end.disabled = false;
  ui.mute.disabled = true;
  ui.mute.textContent = 'Mute microphone';
  ui.mute.setAttribute('aria-pressed', 'false');
  notice('');
  status('Opening microphone…');
  audioStats = { capturedChunks: 0, capturedBytes: 0, sentChunks: 0, sentBytes: 0, maxRms: 0, receivedBytes: 0, forwardedBytes: 0 };
  lastCaptureAt = lastSoundAt = -Infinity; lastRms = 0;
  try {
    if (usesSfu && !window.RTCPeerConnection) throw new Error('This browser does not support WebRTC. Try the WebSocket example.');
    prepareAudioContext();
    inputHealthTimer = setInterval(updateInputHealth, 250);
    updateInputHealth();
    await setupAudio(run);
    if (run !== lifecycle) return;
    status('Starting conversation…');
    const headers = { 'Content-Type': 'application/json' };
    const response = await fetch('/api/session', { method: 'POST', headers, body: JSON.stringify({ transport }), signal: AbortSignal.timeout(20000) });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Session could not start (${response.status}).`);
    if (typeof data.id !== 'string' || typeof data.token !== 'string') throw new Error('The server returned an invalid session.');
    if (run !== lifecycle) {
      // End was clicked during creation. Ask the newly created object to stop.
      const abandoned = new WebSocket(websocketURL(data));
      const timeout = setTimeout(() => abandoned.close(), 5000);
      abandoned.onopen = () => { abandoned.send(JSON.stringify({ type: 'end' })); abandoned.close(1000, 'Ended during startup'); clearTimeout(timeout); };
      abandoned.onerror = () => clearTimeout(timeout);
      return;
    }
    session = data; // Capability remains in memory; never localStorage or logs.
    event('session_created');
    connect(run);
  } catch (error) {
    if (run !== lifecycle) return;
    notice(error.name === 'NotAllowedError' ? 'Microphone permission was denied. Allow it in your browser, then start again.' : error.message);
    endSession(false);
    status('Could not start', 'error');
  } finally { if (run === lifecycle) starting = false; }
}
function websocketURL(credentials) {
  const url = new URL(`/api/session/${encodeURIComponent(credentials.id)}`, location.href);
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  url.searchParams.set('token', credentials.token);
  return url;
}
function completeReady() {
  serverReady = true;
  starting = false;
  ui.mute.disabled = false;
  if (!startedAt) {
    startedAt = performance.now();
    record('startupMs', startedAt - requestedAt, 'metric-startup');
    elapsedTimer = setInterval(() => {
      const seconds = Math.floor((performance.now() - startedAt) / 1000);
      ui['session-time'].textContent = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
    }, 1000);
  }
  status(muted ? 'Microphone muted' : 'Listening', 'live');
  event('ready');
  send({ type: 'ping' });
  updateInputHealth();
}
function failSfu(error, run, ws) {
  if (run !== lifecycle || socket !== ws || !active) return;
  notice(error.name === 'AbortError' ? 'The audio connection timed out. Start again to reconnect.' : error.message);
  endSession();
  status('Audio disconnected', 'error');
}
async function startSfu(run, ws) {
  const credentials = session;
  sfu?.close();
  status('Connecting WebRTC audio…');
  let current;
  try {
    current = new SfuAudioTransport({
      stream, audio: $('remote-audio'), PeerConnection: window.RTCPeerConnection, MediaStreamClass: window.MediaStream,
      signal: async (body, signal) => {
        const response = await fetch(`/api/session/${encodeURIComponent(credentials.id)}/sfu`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Session-Token': credentials.token },
          body: JSON.stringify(body), signal,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || `The audio connection could not be negotiated (${response.status}).`);
        return data;
      },
      onState: (role, state) => { if (sfu === current) { $('metric-queue').textContent = `${role === 'input' ? 'Microphone' : 'Speaker'}: ${state}`; event('webrtc_state', { role, state }); } },
      onError: error => { if (sfu === current) failSfu(error, run, ws); },
      onPlaybackBlocked: blocked => { if (sfu === current) { remotePlaybackBlocked = blocked; updateInputHealth(); } },
    });
    sfu = current;
    await current.publish();
    if (run !== lifecycle || socket !== ws || sfu !== current) { current.close(); return; }
    completeReady();
  } catch (error) {
    if (sfu === current && error.name !== 'AbortError') failSfu(error, run, ws);
  }
}
function connect(run) {
  if (!active || run !== lifecycle) return;
  clearTimeout(reconnectTimer);
  serverReady = false;
  serverState = 'listening';
  const ws = new WebSocket(websocketURL(session));
  socket = ws;
  const connectTimeout = setTimeout(() => { if (!serverReady && socket === ws) ws.close(4000, 'Startup timeout'); }, 35000);
  ws.onopen = () => {
    if (run !== lifecycle || socket !== ws) { ws.close(); return; }
    lastPong = performance.now();
    clearInterval(heartbeatTimer);
    heartbeatTimer = setInterval(() => {
      if (performance.now() - lastPong > 45000) { ws.close(4000, 'Heartbeat timeout'); return; }
      send({ type: 'ping' });
    }, 3000);
  };
  ws.onmessage = e => {
    if (run !== lifecycle || socket !== ws) return;
    try {
      if (typeof e.data !== 'string' || e.data.length > 2_100_000) throw new Error('Invalid server message.');
      const packet = JSON.parse(e.data);
      if (['clear','error','transcript'].includes(packet.type) && Object.hasOwn(packet,'generation')
          && (!Number.isSafeInteger(packet.generation) || packet.generation < knownGeneration)) return;
      if (packet.type !== 'reset' && Number.isSafeInteger(packet.generation)) knownGeneration = Math.max(knownGeneration, packet.generation);
      switch (packet.type) {
        case 'ready':
          clearTimeout(connectTimeout);
          if (usesSfu) startSfu(run, ws);
          else completeReady();
          break;
        case 'sfu_track':
          if (usesSfu && sfu) {
            const current = sfu;
            current.subscribe(packet.generation).catch(error => { if (sfu === current && error.name !== 'AbortError') failSfu(error, run, ws); });
          }
          break;
        case 'audio':
          if (!usesSfu) player?.enqueue(packet);
          break;
        case 'clear':
          resetResponseState();
          clearPlayback(Number.isSafeInteger(packet.generation) ? packet.generation : undefined, 'server_clear');
          break;
        case 'transcript': transcript(packet); break;
        case 'status': {
          serverState = String(packet.state || 'listening');
          const labels = { listening: 'Listening', thinking: 'Thinking…', generating: 'Thinking…', responding: 'Responding…', speaking: 'Speaking', tool: 'Checking availability…', connecting: 'Connecting…' };
          status(muted ? 'Microphone muted' : labels[serverState] || serverState, serverReady ? 'live' : '');
          break;
        }
        case 'reset':
          resetResponseState();
          // A reset begins an ordered WebSocket stream epoch. Old socket
          // messages are independently rejected by socket identity above.
          player?.clear();
          sfu?.clearOutput();
          knownGeneration = Number.isSafeInteger(packet.generation) ? packet.generation : 0;
          if (player) { player.floor = knownGeneration; player.generation = player.floor - 1; }
          partial.clear();
          if (Array.isArray(packet.history)) {
            // Committed speech context replaces the visible transcript after reconnect.
            // It follows Pipecat text progress, independently of playback receipts.
            ui.transcript.replaceChildren(ui['empty-state']);
            ui['empty-state'].hidden = false;
          }
          systemMessage(packet.message || (packet.history?.length ? 'Reconnected. Your conversation history is restored.' : 'Connected. Say hello to start.'));
          if (Array.isArray(packet.history)) for (const turn of packet.history) {
            if (['user', 'assistant'].includes(turn.role)) transcript({ role: turn.role, text: turn.text ?? turn.content, final: true }, { restored: true });
          }
          finalTranscriptAt = null;
          event('session_reset');
          break;
        case 'pong':
          lastPong = performance.now();
          if (packet.audio && Number.isSafeInteger(packet.audio.input_audio_bytes) && packet.audio.input_audio_bytes >= 0) audioStats.receivedBytes = packet.audio.input_audio_bytes;
          if (packet.audio && Number.isSafeInteger(packet.audio.forwarded_audio_bytes) && packet.audio.forwarded_audio_bytes >= 0) audioStats.forwardedBytes = packet.audio.forwarded_audio_bytes;
          updateInputHealth();
          break;
        case 'error':
          resetResponseState();
          notice(packet.message || 'The server reported an error.');
          event('server_error');
          if (packet.recoverable === false) {
            clearTimeout(connectTimeout);
            endSession(false);
            status('Could not connect', 'error');
          }
          break;
        case 'ended': endSession(false); break;
      }
    } catch (error) {
      notice(error.message);
      interrupt('client_audio_or_protocol_error');
    }
  };
  ws.onerror = () => { /* onclose owns bounded recovery. */ };
  ws.onclose = e => {
    clearTimeout(connectTimeout);
    if (run !== lifecycle || socket !== ws) return;
    clearInterval(heartbeatTimer);
    serverReady = false;
    clearPlayback(undefined, 'disconnect');
    sfu?.close(); sfu = null;
    if (!active) return;
    if (e.code === 1000) {
      // The server uses 1012 for recoverable disconnects and 1000 after ending
      // the session. Retrying an ended capability cannot recover the call.
      if (e.reason === 'fifteen_minute_limit') notice('This demo has a 15-minute call limit. Start a new conversation to continue.');
      endSession(false);
      return;
    }
    if ([1008, 4003, 4004].includes(e.code) || connectAttempt >= 4) {
      notice('The conversation disconnected and could not recover. Start again to open a new session.');
      endSession(false);
      status('Disconnected', 'error');
      return;
    }
    const delay = Math.min(4000, 500 * 2 ** connectAttempt++);
    metrics.reconnects++;
    $('metric-reconnect').textContent = String(metrics.reconnects);
    status(`Reconnecting (${connectAttempt}/4)…`);
    event('reconnect', { attempt: connectAttempt, code: e.code });
    reconnectTimer = setTimeout(() => connect(run), delay);
  };
}
function endSession(tellServer = true) {
  if (tellServer) send({ type: 'end' });
  active = starting = serverReady = false;
  lifecycle++;
  clearTimeout(reconnectTimer);
  clearInterval(heartbeatTimer);
  clearInterval(elapsedTimer);
  clearInterval(inputHealthTimer);
  socket?.close(1000, 'Session ended');
  socket = null;
  player?.close();
  player = null;
  sfu?.close(); sfu = null; remotePlaybackBlocked = false;
  if (capture) { capture.port.onmessage = null; capture.port.close(); capture.disconnect(); }
  inputNode?.disconnect();
  silentGain?.disconnect();
  stream?.getTracks().forEach(track => track.stop());
  if (context) { context.onstatechange = null; context.close().catch(() => {}); }
  capture = inputNode = silentGain = stream = context = null;
  session = null;
  partial.clear();
  loudFrames = quietFrames = 0;
  speechActive = false;
  serverState = '';
  ui.start.disabled = false;
  ui.start.innerHTML = '<span aria-hidden="true">↗</span> Start conversation';
  ui.mute.disabled = ui.end.disabled = true;
  status('Conversation ended');
  event('session_ended');
  updateInputHealth();
}
$('resume-audio').addEventListener('click', async () => {
  if (!context || !active) return;
  try {
    const resumeOutput = sfu?.resumePlayback(); // Both requests begin inside this click gesture.
    await context.resume();
    await resumeOutput;
    lastClockAdvanceAt = performance.now(); updateInputHealth();
  }
  catch { notice('Audio could not resume. Check microphone access or try your regular browser.'); }
});
ui.start.addEventListener('click', start);
ui.end.addEventListener('click', () => endSession());
ui.mute.addEventListener('click', () => {
  muted = !muted;
  stream?.getAudioTracks().forEach(track => { track.enabled = !muted; });
  ui.mute.setAttribute('aria-pressed', String(muted));
  ui.mute.textContent = muted ? 'Unmute microphone' : 'Mute microphone';
  status(muted ? 'Microphone muted' : 'Listening', 'live');
  event(muted ? 'muted' : 'unmuted');
});
$('download-metrics').addEventListener('click', async () => {
  const button = $('download-metrics');
  if (button.disabled) return;
  button.disabled = true;
  const credentials = session;
  // Freeze the browser counters now, so End or a new call cannot mix sessions.
  const snapshot = JSON.parse(JSON.stringify(metrics));
  snapshot.exportedAt = new Date().toISOString();
  snapshot.serverDiagnosticsStatus = credentials ? 'unavailable' : 'call_ended_or_not_started';
  try {
    if (credentials) {
      const response = await fetch(`/api/session/${encodeURIComponent(credentials.id)}/diagnostics`, {
        headers: { 'X-Session-Token': credentials.token }, signal: AbortSignal.timeout(5000),
      });
      if (response.ok) {
        snapshot.serverDiagnostics = shareableServerDiagnostics(await response.json());
        snapshot.serverDiagnosticsStatus = 'available';
      }
    }
  } catch { /* Download the browser snapshot even if server diagnostics fail. */ }
  try {
    const blob = new Blob([JSON.stringify({ ...snapshot, limitations: [usesSfu ? 'Speaker mute detaches the remote track; it does not measure acoustic silence or confirm delivery.' : 'Playback clear is a scheduling measurement, not acoustic silence.', usesSfu ? 'First-audio latency and exact chunk playback are not measured for WebRTC.' : 'First audio starts at final transcript and excludes recognition latency.', 'Only the most recent 1000 events per array are retained.', 'Server diagnostics are a later snapshot of this call; absent events do not prove they never occurred.', 'No transcript, audio, capability token, or session identifier is exported.'] }, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'voice-session-measurements.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } finally { button.disabled = false; }
});
window.addEventListener('pagehide', () => { if (active) endSession(); });
