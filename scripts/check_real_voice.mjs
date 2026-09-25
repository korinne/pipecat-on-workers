#!/usr/bin/env node
/** Actual-provider duration/interruption check. Node 22+, no dependencies.
 * Raw PCM16 LE, mono, 16 kHz; no microphone or speaker is opened.
 * DEMO_ACCESS_KEY is read only from the environment. Never selects fixtures.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUTS = path.dirname(PROJECT);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const round = n => Math.round(n * 100) / 100;
class Failure extends Error { constructor(code) { super(code); this.code = code; } }
const fail = code => { throw new Failure(code); };
const RESOURCE_KEYS = ['pipecat_tasks', 'provider_tasks', 'provider_sockets', 'provider_readers',
  'pending_provider_requests', 'queued_provider_bytes', 'unacked_audio_bytes', 'pending_playback_chunks',
  'pending_turn_tasks', 'pending_user_fragments', 'pending_user_chars'];

export function options(args) {
  const out = { duration: 600000, sessions: 2 };
  const keys = { '--base': 'base', '--pcm': 'pcm', '--pcm-second': 'pcmSecond', '--evidence': 'evidence',
    '--duration-ms': 'duration', '--sessions': 'sessions', '--capture-root': 'captureRoot' };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help') out.help = true;
    else if (args[i] === '--validate-input') out.validate = true;
    else if (keys[args[i]] && args[i + 1] && !args[i + 1].startsWith('--')) out[keys[args[i]]] = args[++i];
    else fail('invalid_arguments');
  }
  out.duration = Number(out.duration); out.sessions = Number(out.sessions);
  if (!Number.isInteger(out.duration) || out.duration < 180000 || out.duration > 720000) fail('duration_must_be_180000_to_720000');
  if (![1, 2].includes(out.sessions)) fail('sessions_must_be_1_or_2');
  out.rounds = Math.min(Math.floor(out.duration / 60000), Math.floor(24 / out.sessions));
  return out;
}

export function pcmStats(bytes) {
  if (bytes.length % 2) fail('pcm_length_must_be_even');
  let squares = 0, nonzero = 0;
  for (let i = 0; i < bytes.length; i += 2) { const v = bytes.readInt16LE(i); squares += v * v; nonzero += v !== 0; }
  return { nonzero_samples: nonzero, rms: bytes.length ? round(Math.sqrt(squares / (bytes.length / 2))) : 0 };
}

export function safeDiagnostics(d) {
  const out = { closed: d.closed === true };
  for (const key of [...RESOURCE_KEYS, 'messages', 'stt_connection_generation', 'dropped_audio_bytes', 'turn_end_grace_ms']) {
    if (Number.isFinite(d[key])) out[key] = d[key];
  }
  return out;
}

/** Uses monotonic wall time; a generation clear discards every outstanding receipt. */
export class ReceiptQueue {
  constructor() { this.generation = -1; this.queue = []; this.end = 0; this.seen = new Set(); }
  clear(generation, now) {
    if (generation < this.generation) return;
    this.generation = generation; this.queue.length = 0; this.end = now; this.seen.clear();
  }
  enqueue(message, now) {
    if (message.sample_rate !== 24000 || !Number.isSafeInteger(message.generation) || !Number.isSafeInteger(message.chunk_id)
      || typeof message.data !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(message.data)) fail('invalid_audio_packet');
    if (message.generation < this.generation) return null;
    if (message.generation > this.generation) this.clear(message.generation, now);
    const key = `${message.generation}:${message.chunk_id}`;
    if (this.seen.has(key)) fail('duplicate_audio_packet');
    const pcm = Buffer.from(message.data, 'base64');
    if (!pcm.length || pcm.length % 2 || pcm.length > 4800 || pcm.toString('base64') !== message.data) fail('invalid_pcm_output');
    this.seen.add(key);
    const duration = pcm.length / 48;
    this.end = Math.max(now, this.end) + duration;
    if (this.end - now > 12000) fail('playback_queue_bound_exceeded');
    this.queue.push({ generation: message.generation, chunk_id: message.chunk_id, due: this.end, duration,
      text: typeof message.text === 'string' ? message.text : '' });
    return pcm;
  }
  due(now) { const result = []; while (this.queue.length && this.queue[0].due <= now) result.push(this.queue.shift()); return result; }
}

export async function main(args = process.argv.slice(2)) {
  const opt = options(args);
  if (opt.help) {
    console.log('Usage: node scripts/check_real_voice.mjs --base https://HOST --pcm PATH [--pcm-second PATH] [--evidence PATH] [--duration-ms 600000] [--sessions 2] [--capture-root PATH] [--validate-input]\nDEMO_ACCESS_KEY: environment only. PCM: raw PCM16 LE mono 16000 Hz, 0.2–15 seconds. Duration: 3–12 minutes, at most 24 input turns. Private captures must be outside outputs/. Playback receipts emulate elapsed audio duration; no microphone/speaker measurement.');
    return;
  }
  if (!opt.pcm) fail('pcm_path_required');
  const inputs = await Promise.all([opt.pcm, ...(opt.sessions === 2 ? [opt.pcmSecond || opt.pcm] : [])].map(async name => {
    const data = await fs.readFile(name).catch(() => fail('input_read_failed'));
    if (data.length < 6400 || data.length > 480000 || data.length % 2) fail('input_must_be_pcm16_0.2_to_15_seconds');
    if (['RIFF', 'FORM', 'caff', 'OggS'].includes(data.toString('ascii', 0, 4))) fail('input_has_container_header');
    const stats = pcmStats(data);
    if (stats.rms < 5 || stats.nonzero_samples < 1600) fail('input_is_empty_or_near_silent');
    return data;
  }));
  if (opt.validate) { console.log(JSON.stringify({ input_validated: true, network_used: false, sessions: opt.sessions,
    planned_input_turns: opt.rounds * opt.sessions, duration_ms: opt.duration, input_duration_ms: inputs.map(b => b.length / 32) })); return; }
  let origin;
  try { origin = new URL(opt.base); } catch { fail('invalid_base'); }
  if (origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/'
    || !(origin.protocol === 'https:' || (origin.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)))) fail('base_must_be_https_or_loopback_origin');
  if (!process.env.DEMO_ACCESS_KEY) fail('demo_access_key_environment_required');
  if (typeof WebSocket === 'undefined') fail('node_22_or_newer_required');
  const outsideOutputs = p => { const r = path.relative(OUTPUTS, p); if (!r || (r !== '..' && !r.startsWith('..' + path.sep) && !path.isAbsolute(r))) fail('capture_root_must_be_outside_outputs'); };
  const captureRoot = path.resolve(opt.captureRoot || path.join(process.cwd(), 'work'));
  outsideOutputs(captureRoot); await fs.mkdir(captureRoot, { recursive: true }); outsideOutputs(await fs.realpath(captureRoot));
  const runDir = await fs.mkdtemp(path.join(captureRoot, 'real-voice-soak-')); await fs.chmod(runDir, 0o700);
  const evidenceFile = path.resolve(opt.evidence || path.join(runDir, 'evidence.json'));
  const started = performance.now(); let fatal, stopping = false, clockStart;
  const evidence = { schema: 1, started_at: new Date().toISOString(), deployment_origin: origin.origin,
    passed: false, requested_duration_ms: opt.duration, planned_input_turns: opt.rounds * opt.sessions,
    fixture_provider_requested: false, physical_human_voice_acceptance_passed: false,
    playback_method: 'wall-clock serial PCM duration receipts; no audio device', cross_session_isolation_established: false,
    distinct_recordings_supplied: opt.sessions === 2 && !inputs[0].equals(inputs[1]),
    limitations: ['Prerecorded input; no physical microphone, speaker, echo, intelligibility or acoustic latency measurement.',
      'Separate sessions and optional distinct recordings do not by themselves prove semantic cross-session isolation.',
      'Client interruption is explicit control traffic during queued output, not measured acoustic barge-in.',
      'Zero local resources do not prove remote compute or billing stopped.'], sessions: [], failures: [] };
  const calls = [];
  const markFailure = code => { fatal ||= new Failure(code); };
  const waitFor = async (test, ms, code, checkFatal = true) => {
    const until = performance.now() + ms;
    while (!test()) { if (checkFatal && fatal) throw fatal; if (performance.now() >= until) fail(code); await sleep(20); }
    if (checkFatal && fatal) throw fatal;
  };
  const redact = value => { let s = String(value); for (const key of [process.env.DEMO_ACCESS_KEY, ...calls.flatMap(c => [c.session?.token, c.session?.id])]) if (key) s = s.replaceAll(key, '[redacted]'); return s.slice(0, 12000); };
  const urlFor = (c, suffix = '') => { const u = new URL(`/api/session/${c.session.id}${suffix}`, origin); u.searchParams.set('token', c.session.token); return u; };
  const send = (c, msg) => {
    if (c.ws?.readyState !== WebSocket.OPEN) fail('websocket_not_open');
    if (c.ws.bufferedAmount > 256 * 1024) fail('upload_backpressure');
    c.ws.send(JSON.stringify(msg));
  };
  const connect = async c => {
    c.ready = false; c.reset = undefined; c.reconnecting = false; c.playback.clear(c.playback.generation + 1, performance.now());
    const u = urlFor(c); u.protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(u); c.ws = ws;
    ws.addEventListener('error', () => { if (!stopping && !c.reconnecting && c.ws === ws) markFailure('websocket_transport_error'); });
    ws.addEventListener('close', event => {
      if (c.ws !== ws) return; c.closeCode = event.code; c.ready = false;
      if (!stopping && !c.reconnecting) markFailure('unexpected_websocket_close');
    });
    ws.addEventListener('message', event => {
      if (c.ws !== ws) return;
      try {
        if (typeof event.data !== 'string' || event.data.length > 100000) fail('unexpected_server_frame');
        const m = JSON.parse(event.data), now = performance.now();
        if (m.type === 'ended') { c.result.end_acknowledged = true; return; }
        if (m.type === 'error') { if (c.errors.length < 24) c.errors.push({ elapsed_ms: round(now - started), message: redact(m.message) }); markFailure('server_reported_error'); return; }
        if (stopping || c.reconnecting) return;
        if (m.type === 'reset') {
          if (!Number.isSafeInteger(m.generation) || !Array.isArray(m.history)) fail('invalid_reset');
          c.playback = new ReceiptQueue(); c.playback.clear(m.generation, now); c.reset = m.history;
        }
        if (m.type === 'ready') c.ready = true;
        if (m.type === 'status') c.state = m.state;
        if (m.type === 'clear') { if (!Number.isSafeInteger(m.generation)) fail('invalid_clear'); c.playback.clear(m.generation, now); c.clears++; }
        if (m.type === 'transcript' && m.final === true) {
          if (!['user', 'assistant'].includes(m.role) || typeof m.text !== 'string' || !m.text.trim() || m.text.length > 12000) fail('invalid_transcript');
          if (c.transcripts.length >= 500) fail('transcript_capture_bound_exceeded');
          c.transcripts.push({ elapsed_ms: round(now - started), role: m.role, text: redact(m.text) });
          c.result[m.role === 'user' ? 'user_final_count' : 'assistant_sentence_count']++;
          if (m.role === 'user') c.lastUser = m.text;
        }
        if (m.type === 'audio') {
          const pcm = c.playback.enqueue(m, now); if (!pcm) { c.result.stale_audio_ignored++; return; }
          c.audioBytes += pcm.length; if (c.audioBytes > 40 * 1024 * 1024) fail('audio_capture_bound_exceeded');
          c.audio.push(pcm); c.result.received_chunks++; c.result.nonzero_output_samples += pcmStats(pcm).nonzero_samples;
        }
      } catch (e) { markFailure(e instanceof Failure ? e.code : 'server_message_parse_failed'); }
    });
    await waitFor(() => c.ready && c.reset !== undefined, 30000, 'ready_timeout');
  };
  const inputLoops = []; let receiptTimer;
  const signal = () => markFailure('operator_interrupted');
  process.on('SIGINT', signal); process.on('SIGTERM', signal);
  try {
    for (let i = 0; i < opt.sessions; i++) {
      const result = { ordinal: i + 1, input_duration_ms: inputs[i].length / 32, user_final_count: 0, assistant_sentence_count: 0,
        received_chunks: 0, acknowledged_chunks: 0, acknowledged_duration_ms: 0, completed_sentences: 0,
        nonzero_output_samples: 0, stale_audio_ignored: 0, turns: [], interruptions: 0, recoveries_after_interruption: 0,
        reconnect_history_verified: false, end_sent: false, end_acknowledged: false };
      const c = { result, playback: new ReceiptQueue(), transcripts: [], errors: [], audio: [], audioBytes: 0,
        ready: false, offset: inputs[i].length, input: inputs[i], clears: 0, state: '', receiptTexts: [] };
      calls.push(c); evidence.sessions.push(result);
      const r = await fetch(new URL('/api/session', origin), { method: 'POST', headers: { 'X-Demo-Key': process.env.DEMO_ACCESS_KEY }, redirect: 'error', signal: AbortSignal.timeout(10000) }).catch(() => fail('session_create_transport_failed'));
      if (!r.ok) fail(`session_create_http_${r.status}`);
      c.session = await r.json().catch(() => fail('session_create_invalid_json'));
      if (!/^[a-f0-9]{32}$/.test(c.session.id) || !/^[A-Za-z0-9_-]{32,128}$/.test(c.session.token)) fail('session_create_invalid_capability');
      await connect(c);
      inputLoops.push((async () => {
        while (!stopping && !fatal) {
          if (c.ready && !c.reconnecting) {
            const pcm = Buffer.alloc(2560), count = Math.min(pcm.length, c.input.length - c.offset);
            if (count > 0) { c.input.copy(pcm, 0, c.offset, c.offset + count); c.offset += count; }
            send(c, { type: 'audio', sample_rate: 16000, data: pcm.toString('base64') });
          }
          await sleep(80);
        }
      })().catch(e => markFailure(e instanceof Failure ? e.code : 'input_send_failed')));
    }
    receiptTimer = setInterval(() => {
      if (stopping || fatal) return;
      for (const c of calls) {
        if (!c.ready || c.reconnecting) continue;
        try {
          for (const item of c.playback.due(performance.now())) {
            send(c, { type: 'played', generation: item.generation, chunk_id: item.chunk_id });
            c.result.acknowledged_chunks++; c.result.acknowledged_duration_ms += item.duration;
            if (item.text.trim()) { c.result.completed_sentences++; c.receiptTexts.push(item.text); }
          }
        } catch (e) { markFailure(e instanceof Failure ? e.code : 'receipt_send_failed'); }
      }
    }, 10);
    clockStart = performance.now();
    for (let turn = 0; turn < opt.rounds; turn++) {
      await waitFor(() => performance.now() >= clockStart + turn * 60000, 61000, 'schedule_timeout');
      await Promise.all(calls.map(async c => {
        const before = { user: c.result.user_final_count, audio: c.result.received_chunks, assistant: c.result.assistant_sentence_count,
          sentences: c.result.completed_sentences, nonzero: c.result.nonzero_output_samples };
        const turnStart = performance.now(), interrupted = turn % 2 === 0 && turn < opt.rounds - 1;
        c.offset = 0;
        await waitFor(() => c.result.user_final_count > before.user && c.result.received_chunks > before.audio
          && c.result.assistant_sentence_count > before.assistant && c.playback.queue.length > 0, 45000, 'real_turn_response_timeout');
        const record = { number: turn + 1, response_ms: round(performance.now() - turnStart), interrupted };
        if (interrupted) {
          const oldClears = c.clears, oldGeneration = c.playback.generation;
          record.pending_playback_ms_at_interrupt = round(c.playback.end - performance.now());
          if (record.pending_playback_ms_at_interrupt <= 0) fail('no_pending_audio_at_interrupt');
          c.playback.clear(oldGeneration + 1, performance.now()); send(c, { type: 'interrupt' });
          await waitFor(() => c.clears > oldClears && c.playback.generation > oldGeneration, 10000, 'interrupt_clear_timeout');
          c.result.interruptions++; c.needsRecovery = true;
        } else {
          await waitFor(() => c.result.completed_sentences > before.sentences && c.state === 'listening' && !c.playback.queue.length,
            Math.max(1000, 55000 - (performance.now() - turnStart)), 'completed_playback_timeout');
          if (c.result.nonzero_output_samples <= before.nonzero) fail('provider_output_is_silent');
          if (c.needsRecovery) { c.result.recoveries_after_interruption++; c.needsRecovery = false; record.recovery_verified = true; }
        }
        record.elapsed_ms = round(performance.now() - turnStart); c.result.turns.push(record);
      }));
      if (turn === 1) {
        const c = calls[0], expectedUser = c.lastUser, expectedAssistant = c.receiptTexts.at(-1);
        c.reconnecting = true; c.ready = false; c.playback.clear(c.playback.generation + 1, performance.now());
        c.ws.close(1000, 'Planned reconnect'); await waitFor(() => c.ws.readyState === WebSocket.CLOSED, 10000, 'disconnect_timeout');
        await sleep(500); await connect(c);
        if (!expectedUser || !expectedAssistant || !c.reset.some(m => m.role === 'user' && String(m.content ?? m.text).includes(expectedUser))
          || !c.reset.some(m => m.role === 'assistant' && String(m.content ?? m.text).includes(expectedAssistant))) fail('reconnect_history_mismatch');
        c.result.reconnect_history_verified = true;
      }
      console.log(JSON.stringify({ progress: true, elapsed_ms: round(performance.now() - clockStart), input_turns: calls.reduce((n, c) => n + c.result.turns.length, 0), interruptions: calls.reduce((n, c) => n + c.result.interruptions, 0) }));
    }
    await waitFor(() => performance.now() - clockStart >= opt.duration, 61000, 'duration_timeout');
    evidence.active_duration_ms = round(performance.now() - clockStart);
    if (!calls[0].result.reconnect_history_verified || calls.some(c => !c.result.interruptions || c.needsRecovery
      || c.result.recoveries_after_interruption !== c.result.interruptions)) fail('recovery_checks_incomplete');
  } catch (e) { const code = e instanceof Failure ? e.code : 'unexpected_runtime_failure'; markFailure(code); evidence.failures.push(code); }
  finally {
    stopping = true; clearInterval(receiptTimer); await Promise.all(inputLoops);
    await Promise.all(calls.map(async c => {
      c.playback.queue.length = 0;
      if (c.ws?.readyState === WebSocket.OPEN) {
        try { send(c, { type: 'end' }); c.result.end_sent = true; await waitFor(() => c.ws.readyState === WebSocket.CLOSED, 10000, 'end_close_timeout', false); }
        catch { evidence.failures.push('end_close_failed'); }
      }
      if (c.ws && c.ws.readyState !== WebSocket.CLOSED) { try { c.ws.close(); } catch {} }
      c.result.close_code = c.closeCode;
      if (!c.result.end_sent || !c.result.end_acknowledged || c.closeCode !== 1000) evidence.failures.push('normal_end_not_verified');
      if (c.session?.token) {
        const until = performance.now() + 15000;
        do {
          try { const r = await fetch(urlFor(c, '/diagnostics'), { redirect: 'error', signal: AbortSignal.timeout(4000) });
            if (r.ok) c.result.cleanup = safeDiagnostics(await r.json()); } catch {}
          c.result.cleanup_verified = c.result.cleanup?.closed === true && RESOURCE_KEYS.every(k => c.result.cleanup[k] === 0);
          if (c.result.cleanup_verified) break; await sleep(500);
        } while (performance.now() < until);
        if (!c.result.cleanup_verified) evidence.failures.push('cleanup_not_verified');
      }
      c.result.acknowledged_duration_ms = round(c.result.acknowledged_duration_ms);
      c.result.received_audio_duration_ms = round(c.audioBytes / 48);
      const prefix = path.join(runDir, `session-${c.result.ordinal}`);
      await fs.writeFile(prefix + '-transcripts.private.json', JSON.stringify(c.transcripts, null, 2), { mode: 0o600 });
      await fs.writeFile(prefix + '-errors.private.json', JSON.stringify(c.errors, null, 2), { mode: 0o600 });
      await fs.writeFile(prefix + '-received-24000hz-mono-pcm16le.private.pcm', Buffer.concat(c.audio), { mode: 0o600 });
    }));
    process.removeListener('SIGINT', signal); process.removeListener('SIGTERM', signal);
    if (fatal) evidence.failures.push(fatal.code);
    evidence.failures = [...new Set(evidence.failures)]; evidence.finished_at = new Date().toISOString();
    evidence.elapsed_ms = round(performance.now() - started);
    evidence.passed = !evidence.failures.length && evidence.active_duration_ms >= opt.duration;
    await fs.mkdir(path.dirname(evidenceFile), { recursive: true });
    await fs.writeFile(evidenceFile, JSON.stringify(evidence, null, 2) + '\n', { mode: 0o600 });
    console.log(JSON.stringify({ passed: evidence.passed, failures: evidence.failures,
      physical_human_voice_acceptance_passed: false, evidence_file: path.relative(process.cwd(), evidenceFile),
      private_capture_directory: path.relative(process.cwd(), runDir) }));
    if (!evidence.passed) process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main().catch(e => { console.error(JSON.stringify({ passed: false,
    failure: e instanceof Failure ? e.code : 'local_setup_or_output_failed' })); process.exitCode = 1; });
}
