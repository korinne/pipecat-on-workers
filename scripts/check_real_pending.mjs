#!/usr/bin/env node
/** Two prerecorded inputs per selected fresh actual-provider session. No fixture events.
 * Interrupt at tool/thinking status before audio, reject canceled output, recover,
 * await response completion and elapsed audio receipts, then verify End cleanup.
 * No microphone or speaker is opened.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ReceiptQueue, pcmStats, safeDiagnostics } from './check_real_voice.mjs';
import { isCompletedResponse } from './smoke_real_voice.mjs';

const OUTPUTS = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const RESOURCE_KEYS = ['pipecat_tasks', 'provider_tasks', 'provider_sockets', 'provider_readers',
  'pending_provider_requests', 'pending_turn_requests', 'queued_output_bytes', 'queued_provider_bytes', 'unacked_audio_bytes', 'pending_playback_chunks',
  'pending_turn_tasks', 'pending_user_fragments', 'pending_user_chars'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const round = n => Math.round(n * 100) / 100;
class Failure extends Error { constructor(code) { super(code); this.code = code; } }
const fail = code => { throw new Failure(code); };
const errorCode = e => typeof e?.code === 'string' && /^[a-z][a-z0-9_]{0,80}$/.test(e.code) ? e.code : 'unexpected_runtime_failure';

export function options(args) {
  const out = {}, keys = { '--base': 'base', '--pcm': 'pcm', '--tool-pcm': 'toolPcm', '--evidence': 'evidence', '--capture-root': 'captureRoot', '--case': 'case' };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--help') out.help = true;
    else if (args[i] === '--validate-input') out.validate = true;
    else if (keys[args[i]] && args[i + 1] && !args[i + 1].startsWith('--')) out[keys[args[i]]] = args[++i];
    else fail('invalid_arguments');
  }
  if (out.case !== undefined && !['all', 'tool', 'thinking'].includes(out.case)) fail('invalid_case');
  return out;
}

/** Tracks server generations independently of local playback dropping stale data. */
export class PendingCancellation {
  constructor(target) { this.target = target; this.generation = undefined; this.clearAt = undefined; }
  status(message, now, audioCount) {
    if (this.generation !== undefined || message.state !== this.target) return false;
    if (audioCount !== 0) fail('audio_arrived_before_pending_interrupt');
    if (!Number.isSafeInteger(message.generation)) fail('invalid_status_generation');
    this.generation = message.generation; this.requestedAt = now;
    return true;
  }
  clear(message, now) {
    if (this.generation !== undefined && message.generation > this.generation && this.clearAt === undefined) this.clearAt = now;
  }
  audio(message) {
    if (this.clearAt !== undefined && message.generation <= this.generation) fail('canceled_generation_audio_after_clear');
  }
}

export async function main(args = process.argv.slice(2)) {
  const opt = options(args);
  if (opt.help) {
    console.log('Usage: node scripts/check_real_pending.mjs --base https://HOST --pcm RECOVERY.pcm --tool-pcm APPOINTMENT.pcm [--case all|tool|thinking] [--evidence PATH] [--capture-root PATH] [--validate-input]\nInputs: headerless PCM16 LE mono 16000 Hz, 0.2–15 seconds. Two inputs per fresh session; four total for default --case all, two for one selected case. Wall-clock playback receipts only; no physical microphone/speaker measurement. Active checks stop at 165 seconds; cleanup stops by 175 seconds. Private captures must be outside outputs/.');
    return;
  }
  const targets = !opt.case || opt.case === 'all' ? ['tool', 'thinking'] : [opt.case];
  const plannedInputTurns = targets.length * 2;
  if (!opt.pcm || !opt.toolPcm) fail('both_pcm_paths_required');
  const [normal, tool] = await Promise.all([opt.pcm, opt.toolPcm].map(async name => {
    const data = await fs.readFile(name).catch(() => fail('input_read_failed'));
    if (data.length % 2 || data.length < 6400 || data.length > 480000) fail('input_must_be_pcm16_0_2_to_15_seconds');
    if (['RIFF', 'FORM', 'caff', 'OggS'].includes(data.toString('ascii', 0, 4))) fail('input_has_container_header');
    const stats = pcmStats(data); if (stats.rms < 5 || stats.nonzero_samples < 1600) fail('input_is_empty_or_near_silent');
    return data;
  }));
  if (opt.validate) { console.log(JSON.stringify({ input_validated: true, network_used: false, planned_input_turns: plannedInputTurns,
    recovery_duration_ms: normal.length / 32, tool_duration_ms: tool.length / 32 })); return; }
  let origin;
  try { origin = new URL(opt.base); } catch { fail('invalid_base'); }
  if (origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/'
    || !(origin.protocol === 'https:' || (origin.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(origin.hostname)))) fail('base_must_be_https_or_loopback_origin');
  if (typeof WebSocket === 'undefined') fail('node_22_or_newer_required');
  const outsideOutputs = p => { const r = path.relative(OUTPUTS, p); if (!r || (r !== '..' && !r.startsWith('..' + path.sep) && !path.isAbsolute(r))) fail('capture_root_must_be_outside_outputs'); };
  const captureRoot = path.resolve(opt.captureRoot || path.join(process.cwd(), 'work'));
  outsideOutputs(captureRoot); await fs.mkdir(captureRoot, { recursive: true }); outsideOutputs(await fs.realpath(captureRoot));
  const runDir = await fs.mkdtemp(path.join(captureRoot, 'real-pending-')); await fs.chmod(runDir, 0o700);
  const evidenceFile = path.resolve(opt.evidence || path.join(runDir, 'evidence.json'));
  const evidence = { schema: 2, started_at: new Date().toISOString(), deployment_origin: origin.origin,
    passed: false, selected_cases: targets, planned_input_turns: plannedInputTurns, fixture_provider_requested: false, physical_human_voice_acceptance_passed: false,
    interruption_method: 'explicit client control immediately on tool/thinking status, before audio',
    playback_method: 'elapsed serial PCM duration receipts; no audio device',
    limitations: ['Pending status is observed at the client; cancellation of remote model compute or billing is not established.',
      'Tool case exercises the application fictional availability lookup, not a remote booking API.',
      'Prerecorded input and explicit client control do not measure microphone capture, acoustic barge-in, speakers or intelligibility.',
      'One-second post-clear observation and successful recovery do not prove indefinite absence of late remote work.'], cases: [], failures: [] };
  const started = performance.now(), activeDeadline = started + 165000, cleanupDeadline = started + 175000;
  let fatal;
  const markFailure = code => { fatal ||= new Failure(code); };
  const signal = () => markFailure('operator_interrupted');
  process.on('SIGINT', signal); process.on('SIGTERM', signal);
  const waitFor = async (test, ms, code, cleanup = false) => {
    const until = Math.min(performance.now() + ms, cleanup ? cleanupDeadline : activeDeadline);
    while (!test()) { if (!cleanup && fatal) throw fatal; if (performance.now() >= until) fail(code); await sleep(10); }
    if (!cleanup && fatal) throw fatal;
  };
  try {
    for (const target of targets) {
      if (fatal) throw fatal;
      const result = { target_status: target, input_turns: 0, interrupted_before_audio: false, clear_observed: false,
        post_clear_observation_ms: 0, canceled_audio_packets_after_clear: 0, recovery_user_finals: 0,
        recovery_assistant_sentences: 0, recovery_audio_chunks: 0, recovery_nonzero_samples: 0,
        recovery_completed_responses: 0, recovery_acknowledged_chunks: 0, acknowledged_duration_ms: 0, end_sent: false, end_acknowledged: false, failures: [] };
      evidence.cases.push(result);
      const cancellation = new PendingCancellation(target), playback = new ReceiptQueue();
      const transcripts = [], privateErrors = [], audio = [];
      let session, ws, ready = false, closing = false, stopInput = false, inputTask, receiptTimer;
      let replyGeneration, listeningGeneration;
      let phase = 'pending', clip = target === 'tool' ? tool : normal, offset = clip.length, received = 0, bytes = 0, closeCode;
      const redact = value => { let text = String(value); for (const secret of [session?.token, session?.id]) if (secret) text = text.replaceAll(secret, '[redacted]'); return text.slice(0, 12000); };
      const sessionURL = suffix => { const u = new URL(`/api/session/${session.id}${suffix}`, origin); u.searchParams.set('token', session.token); return u; };
      const send = value => { if (ws?.readyState !== WebSocket.OPEN) fail('websocket_not_open'); if (ws.bufferedAmount > 262144) fail('upload_backpressure'); ws.send(JSON.stringify(value)); };
      try {
        const r = await fetch(new URL('/api/session', origin), { method: 'POST', redirect: 'error', signal: AbortSignal.timeout(5000) }).catch(() => fail('session_create_transport_failed'));
        if (!r.ok) fail(`session_create_http_${r.status}`);
        session = await r.json().catch(() => fail('session_create_invalid_json'));
        if (!/^[a-f0-9]{32}$/.test(session.id) || !/^[A-Za-z0-9_-]{32,128}$/.test(session.token)) fail('session_create_invalid_capability');
        const u = sessionURL(''); u.protocol = origin.protocol === 'https:' ? 'wss:' : 'ws:'; ws = new WebSocket(u);
        ws.addEventListener('error', () => { if (!closing) markFailure('websocket_transport_error'); });
        ws.addEventListener('close', e => { closeCode = e.code; if (!closing) markFailure('unexpected_websocket_close'); });
        ws.addEventListener('message', event => {
          try {
            if (typeof event.data !== 'string' || event.data.length > 100000) fail('unexpected_server_frame');
            const m = JSON.parse(event.data), now = performance.now();
            if (m.type === 'ended') { result.end_acknowledged = true; return; }
            if (m.type === 'error') { if (privateErrors.length < 12) privateErrors.push({ elapsed_ms: round(now - started), message: redact(m.message) }); markFailure('server_reported_error'); return; }
            if (closing) return;
            if (m.type === 'ready') ready = true;
            if (m.type === 'reset' || m.type === 'clear') {
              if (!Number.isSafeInteger(m.generation)) fail('invalid_generation');
              playback.clear(m.generation, now);
              if (m.type === 'clear') { cancellation.clear(m, now); result.clear_observed = cancellation.clearAt !== undefined; }
            }
            if (m.type === 'status' && phase === 'recovery' && Number.isSafeInteger(m.generation)) {
              if (m.state === 'thinking') replyGeneration = m.generation;
              if (m.state === 'listening') listeningGeneration = m.generation;
            }
            if (m.type === 'status' && phase === 'pending' && cancellation.status(m, now, received)) {
              playback.clear(m.generation + 1, now); send({ type: 'interrupt' }); result.interrupted_before_audio = true;
            }
            if (m.type === 'transcript' && m.final === true) {
              if (!['user', 'assistant'].includes(m.role) || typeof m.text !== 'string' || !m.text.trim() || m.text.length > 12000) fail('invalid_transcript');
              if (transcripts.length >= 50) fail('transcript_capture_bound_exceeded');
              transcripts.push({ elapsed_ms: round(now - started), phase, role: m.role, text: redact(m.text) });
              if (phase === 'recovery') result[m.role === 'user' ? 'recovery_user_finals' : 'recovery_assistant_sentences']++;
            }
            if (m.type === 'audio') {
              try { cancellation.audio(m); } catch (e) { result.canceled_audio_packets_after_clear++; throw e; }
              received++;
              const pcm = playback.enqueue(m, now); if (!pcm) return;
              bytes += pcm.length; if (bytes > 5 * 1024 * 1024) fail('audio_capture_bound_exceeded'); audio.push(pcm);
              if (phase === 'recovery') { result.recovery_audio_chunks++; result.recovery_nonzero_samples += pcmStats(pcm).nonzero_samples; }
            }
          } catch (e) { markFailure(errorCode(e)); }
        });
        await waitFor(() => ready, 30000, 'ready_timeout');
        inputTask = (async () => {
          while (!stopInput && !fatal) {
            const pcm = Buffer.alloc(2560), count = Math.min(pcm.length, clip.length - offset);
            if (count > 0) { clip.copy(pcm, 0, offset, offset + count); offset += count; }
            send({ type: 'audio', sample_rate: 16000, data: pcm.toString('base64') }); await sleep(80);
          }
        })().catch(e => markFailure(errorCode(e)));
        receiptTimer = setInterval(() => {
          if (closing || fatal || phase !== 'recovery') return;
          try { for (const item of playback.due(performance.now())) {
            send({ type: 'played', generation: item.generation, chunk_id: item.chunk_id });
            result.acknowledged_duration_ms += item.duration;
            result.recovery_acknowledged_chunks++;
          } } catch (e) { markFailure(errorCode(e)); }
        }, 10);
        offset = 0; result.input_turns++;
        await waitFor(() => result.clear_observed, 35000, 'pending_interrupt_or_clear_timeout');
        result.clear_dispatch_observed_ms = round(cancellation.clearAt - cancellation.requestedAt);
        await waitFor(() => performance.now() - cancellation.clearAt >= 1000, 1200, 'post_clear_observation_timeout');
        result.post_clear_observation_ms = round(performance.now() - cancellation.clearAt);
        if (offset < clip.length) fail('pending_turn_audio_not_finished');
        phase = 'recovery'; clip = normal; offset = 0; result.input_turns++;
        await waitFor(() => result.recovery_nonzero_samples > 0 && isCompletedResponse({
          userFinals: result.recovery_user_finals, assistantSentences: result.recovery_assistant_sentences,
          received: result.recovery_audio_chunks, acknowledged: result.recovery_acknowledged_chunks,
          queueLength: playback.queue.length, replyGeneration, listeningGeneration, generation: playback.generation,
        }), 40000, 'real_recovery_timeout');
        result.recovery_completed_responses = 1;
        result.recovery_verified = true;
      } catch (e) { const code = errorCode(e); result.failures.push(code); markFailure(code); }
      finally {
        closing = true; stopInput = true; clearInterval(receiptTimer); playback.queue.length = 0; if (inputTask) await inputTask;
        if (ws?.readyState === WebSocket.OPEN) {
          try { send({ type: 'end' }); result.end_sent = true; await waitFor(() => ws.readyState === WebSocket.CLOSED, 5000, 'end_close_timeout', true); }
          catch { result.failures.push('end_close_failed'); }
        }
        if (ws && ws.readyState !== WebSocket.CLOSED) { try { ws.close(); } catch {} }
        result.close_code = closeCode;
        if (!result.end_sent || !result.end_acknowledged || closeCode !== 1000) result.failures.push('normal_end_not_verified');
        if (session?.token) {
          const until = Math.min(performance.now() + 7000, cleanupDeadline);
          do {
            if (performance.now() >= until) break;
            try { const r = await fetch(sessionURL('/diagnostics'), { redirect: 'error', signal: AbortSignal.timeout(Math.max(1, Math.min(2000, Math.ceil(until - performance.now())))) });
              if (r.ok) result.cleanup = safeDiagnostics(await r.json()); } catch {}
            result.cleanup_verified = result.cleanup?.closed === true && RESOURCE_KEYS.every(k => result.cleanup[k] === 0);
            if (result.cleanup_verified) break; await sleep(200);
          } while (performance.now() < until);
          if (!result.cleanup_verified) result.failures.push('cleanup_not_verified');
        }
        if (fatal) result.failures.push(fatal.code);
        result.failures = [...new Set(result.failures)]; result.acknowledged_duration_ms = round(result.acknowledged_duration_ms);
        result.passed = !result.failures.length && result.recovery_verified === true && result.interrupted_before_audio
          && result.clear_observed && result.post_clear_observation_ms >= 1000 && result.canceled_audio_packets_after_clear === 0 && result.cleanup_verified;
        const prefix = path.join(runDir, target);
        await fs.writeFile(prefix + '-transcripts.private.json', JSON.stringify(transcripts, null, 2), { mode: 0o600 });
        await fs.writeFile(prefix + '-errors.private.json', JSON.stringify(privateErrors, null, 2), { mode: 0o600 });
        await fs.writeFile(prefix + '-received-24000hz-mono-pcm16le.private.pcm', Buffer.concat(audio), { mode: 0o600 });
      }
      if (!result.passed) fail('pending_case_failed');
    }
  } catch (e) { evidence.failures.push(errorCode(e)); }
  finally {
    process.removeListener('SIGINT', signal); process.removeListener('SIGTERM', signal);
    if (fatal) evidence.failures.push(fatal.code); evidence.failures = [...new Set(evidence.failures)];
    evidence.input_turns = evidence.cases.reduce((n, c) => n + c.input_turns, 0);
    evidence.passed = !evidence.failures.length && evidence.cases.length === targets.length && evidence.cases.every(c => c.passed) && evidence.input_turns === plannedInputTurns;
    evidence.elapsed_ms = round(performance.now() - started); evidence.finished_at = new Date().toISOString();
    await fs.mkdir(path.dirname(evidenceFile), { recursive: true });
    await fs.writeFile(evidenceFile, JSON.stringify(evidence, null, 2) + '\n', { mode: 0o600 });
    console.log(JSON.stringify({ passed: evidence.passed, failures: evidence.failures,
      cases: evidence.cases.map(c => ({ target_status: c.target_status, passed: c.passed, failures: c.failures })),
      physical_human_voice_acceptance_passed: false, evidence_file: path.relative(process.cwd(), evidenceFile), private_capture_directory: path.relative(process.cwd(), runDir) }));
    if (!evidence.passed) process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main().catch(e => { console.error(JSON.stringify({ passed: false, failure: errorCode(e) })); process.exitCode = 1; });
}
