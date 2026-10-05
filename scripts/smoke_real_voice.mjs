#!/usr/bin/env node
/**
 * One bounded, actual-provider turn. Node 22+, no added dependencies.
 *
 * node scripts/smoke_real_voice.mjs --base https://YOUR-WORKER.workers.dev \
 *   --pcm work/prompt.pcm --speech-source macos-say
 *
 * Input is HEADERLESS signed little-endian PCM16, mono, 16000 Hz, 0.2–15 s.
 * --speech-source: macos-say | prerecorded-tts | prerecorded-human | prerecorded-unspecified
 * --capture-root: private capture parent (default: working directory/work).
 * --evidence: optional sanitized JSON destination (default: private run folder).
 * --validate-input: check the input and exit without making network requests.
 * --expect-single-user-turn: require one final user transcript and no reply before input ends.
 * --help: print usage without making network requests.
 *
 * Transcripts, server errors and received PCM are private captures outside outputs/. No audio
 * device is opened: played receipts emulate elapsed playback duration only.
 * This tests actual provider integration, not physical-human voice acceptance.
 */
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const PROJECT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const OUTPUTS = path.dirname(PROJECT);
const INPUT_RATE = 16000, OUTPUT_RATE = 24000, CHUNK_MS = 80;
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const round = value => Math.round(value * 100) / 100;
class Failure extends Error { constructor(code) { super(code); this.code = code; } }
const fail = code => { throw new Failure(code); };

export function options(args) {
  const result = { source: 'prerecorded-unspecified' };
  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--help') result.help = true;
    else if (arg === '--validate-input') result.validate = true;
    else if (arg === '--expect-single-user-turn') result.expectSingleUserTurn = true;
    else if (['--base', '--pcm', '--speech-source', '--evidence', '--capture-root'].includes(arg)) {
      if (!args[i + 1] || args[i + 1].startsWith('--')) fail('missing_argument');
      result[{ '--base': 'base', '--pcm': 'pcm', '--speech-source': 'source', '--evidence': 'evidence', '--capture-root': 'captureRoot' }[arg]] = args[++i];
    } else fail('unknown_argument');
  }
  if (!['macos-say', 'prerecorded-tts', 'prerecorded-human', 'prerecorded-unspecified'].includes(result.source)) fail('invalid_speech_source');
  return result;
}

export function isPrematureReply(message, receivedAt, inputDoneAt) {
  const reply = message.type === 'audio' || (message.type === 'transcript' && message.role === 'assistant');
  return reply && (inputDoneAt === undefined || receivedAt < inputDoneAt);
}

function pcmStats(bytes) {
  let sum = 0, peak = 0, nonzero = 0;
  for (let i = 0; i < bytes.length; i += 2) {
    const v = bytes.readInt16LE(i);
    sum += v * v; peak = Math.max(peak, Math.abs(v)); nonzero += v !== 0;
  }
  return { samples: bytes.length / 2, nonzero_samples: nonzero, peak,
    rms: bytes.length ? round(Math.sqrt(sum / (bytes.length / 2))) : 0 };
}

function wave(pcm) {
  const h = Buffer.alloc(44);
  h.write('RIFF'); h.writeUInt32LE(pcm.length + 36, 4); h.write('WAVEfmt ', 8);
  h.writeUInt32LE(16, 16); h.writeUInt16LE(1, 20); h.writeUInt16LE(1, 22);
  h.writeUInt32LE(OUTPUT_RATE, 24); h.writeUInt32LE(OUTPUT_RATE * 2, 28);
  h.writeUInt16LE(2, 32); h.writeUInt16LE(16, 34); h.write('data', 36);
  h.writeUInt32LE(pcm.length, 40);
  return Buffer.concat([h, pcm]);
}

const RESOURCE_KEYS = ['pipecat_tasks', 'provider_tasks', 'provider_sockets', 'provider_readers',
  'pending_provider_requests', 'queued_provider_bytes', 'unacked_audio_bytes', 'pending_playback_chunks',
  'pending_turn_tasks', 'pending_user_fragments', 'pending_user_chars'];
const METRIC_FIELDS = {
  startup: ['startup_ms'], user_end: [], generation_started: ['generation'],
  first_audio: ['generation', 'response_ms'], server_clear: ['generation', 'dispatch_ms'],
  generation_cancelled: [], provider_error: ['recoverable'], closed: [],
};
function safeDiagnostics(d) {
  const out = { closed: d.closed === true };
  for (const key of [...RESOURCE_KEYS, 'messages', 'stt_connection_generation', 'dropped_audio_bytes', 'turn_timeout_secs']) {
    if (Number.isFinite(d[key])) out[key] = d[key];
  }
  out.metrics = (Array.isArray(d.metrics) ? d.metrics : []).flatMap(m => {
    if (!Object.hasOwn(METRIC_FIELDS, m.event)) return [];
    const v = { event: m.event };
    for (const key of ['elapsed_ms', ...METRIC_FIELDS[m.event]]) {
      if (Number.isFinite(m[key]) || typeof m[key] === 'boolean') v[key] = m[key];
    }
    return [v];
  });
  return out;
}

async function main() {
  const opt = options(process.argv.slice(2));
  if (opt.help) {
    console.log('Usage: node PATH/TO/scripts/smoke_real_voice.mjs --base https://HOST --pcm work/prompt.pcm [--speech-source macos-say|prerecorded-tts|prerecorded-human|prerecorded-unspecified] [--capture-root PATH] [--evidence PATH] [--validate-input] [--expect-single-user-turn]\nHeaderless PCM16 LE, mono, 16000 Hz, 0.2–15 seconds. The single-turn flag requires exactly one final user transcript and no assistant transcript/audio before the paced input ends. No audio device playback/recording. Private captures default to the working directory/work and must be outside outputs/.');
    return;
  }
  if (!opt.pcm) fail('pcm_path_required');
  const input = await fs.readFile(opt.pcm).catch(() => fail('input_read_failed'));
  if (input.length % 2 || input.length < 6400 || input.length > 480000) fail('input_must_be_raw_pcm16_0.2_to_15_seconds');
  if (['RIFF', 'FORM', 'caff', 'OggS'].includes(input.toString('ascii', 0, 4))) fail('input_has_container_header');
  const inputStats = pcmStats(input);
  if (inputStats.rms < 5 || inputStats.nonzero_samples < INPUT_RATE / 10) fail('input_is_empty_or_near_silent');
  if (opt.validate) {
    console.log(JSON.stringify({ input_validated: true, network_used: false, source: opt.source, expect_single_user_turn: Boolean(opt.expectSingleUserTurn),
      assumed_format: 'headerless PCM16 LE mono 16000 Hz', duration_ms: input.length / 32, ...inputStats }));
    return;
  }
  if (!opt.base) fail('base_required');
  let origin;
  try { origin = new URL(opt.base); } catch { fail('invalid_base'); }
  if (origin.protocol !== 'https:' || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') fail('base_must_be_https_origin_without_credentials_or_query');
  if (typeof WebSocket === 'undefined') fail('node_22_or_newer_required');
  const workDir = path.resolve(opt.captureRoot || path.resolve(process.cwd(), 'work'));
  const ensureOutsideOutputs = directory => {
    const relative = path.relative(OUTPUTS, directory);
    if (!relative || (relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative))) fail('capture_root_must_be_outside_outputs');
  };
  ensureOutsideOutputs(workDir);
  await fs.mkdir(workDir, { recursive: true });
  ensureOutsideOutputs(await fs.realpath(workDir));
  const runDir = await fs.mkdtemp(path.join(workDir, 'real-voice-'));
  await fs.chmod(runDir, 0o700);
  const evidenceFile = opt.evidence ? path.resolve(opt.evidence) : path.join(runDir, 'evidence.json');
  const evidence = {
    schema: 1, started_at: new Date().toISOString(), deployment_origin: origin.origin, passed: false,
    scope: 'Actual deployed capability WebSocket and configured STT, LLM, TTS providers; prerecorded input; wall-clock playback-receipt emulation.',
    physical_human_voice_acceptance_passed: false, fixture_provider_requested: false,
    acceptance_kind: opt.expectSingleUserTurn ? 'single_user_turn_pause_check' : 'generic_provider_integration',
    pause_acceptance: { requested: Boolean(opt.expectSingleUserTurn), passed: opt.expectSingleUserTurn ? false : null,
      premature_reply_observed: opt.expectSingleUserTurn ? false : null,
      definition: 'Exactly one final user transcript and no assistant transcript or audio received before the final input PCM sample completes its paced duration. This checks this recording only, not general natural-speech turn quality.' },
    input: { source: opt.source, format: 'PCM16 LE mono 16000 Hz', duration_ms: input.length / 32, ...inputStats },
    playback: { method: 'serial elapsed PCM duration, no audio device', received_chunks: 0, acknowledged_chunks: 0, acknowledged_duration_ms: 0, completed_sentences: 0 },
    transcript: { user_final_count: 0, assistant_sentence_count: 0 },
    latency_ms: {},
    latency_definitions: { first_audio_after_final_transcript: 'First accepted audio packet receipt minus the latest final user transcript receipt preceding that packet. The transcript timestamp is frozen when first audio arrives; this is not acoustic response latency.' },
    end: { sent: false, acknowledged: false, socket_closed: false },
    limitations: ['No microphone permissions, capture/resampling, speakers, headphones, echo or audible latency measured.',
      'No human assessment of intelligibility or semantic correctness; inspect private captures.',
      'One short response only; interruption, multi-turn memory, concurrent calls and soak acceptance remain untested.',
      'Zero local resource counts do not prove remote compute or billing stopped.'],
    failures: [],
  };
  const started = performance.now(), elapsed = () => performance.now() - started;
  const transcripts = [], privateErrors = [], audio = [], receiptTrace = [], queue = [], seen = new Set();
  let session, socket, fatal, ready = false, closing = false, generation = -1;
  let scheduledEnd = 0, userFinalAt, userFinalBeforeFirstAudioAt, inputDoneAt, firstAudioAt, receiptTimer, heartbeat;
  let inputTask, stopInput = false, totalOutputBytes = 0;
  const markFailure = code => { fatal ||= new Failure(code); };
  const send = value => {
    if (socket?.readyState !== WebSocket.OPEN) fail('websocket_not_open');
    if (socket.bufferedAmount > 256 * 1024) fail('upload_backpressure');
    socket.send(JSON.stringify(value));
  };
  const sessionURL = suffix => {
    const url = new URL(`/api/session/${session.id}${suffix}`, origin);
    url.searchParams.set('token', session.token);
    return url;
  };
  const waitFor = async (test, ms, code, checkFatal = true) => {
    const end = performance.now() + ms;
    while (!test()) {
      if (checkFatal && fatal) throw fatal;
      if (performance.now() >= end) fail(code);
      await sleep(20);
    }
    if (checkFatal && fatal) throw fatal;
  };
  const interrupted = () => markFailure('operator_interrupted');
  process.on('SIGINT', interrupted); process.on('SIGTERM', interrupted);
  try {
    const response = await fetch(new URL('/api/session', origin), {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10000),
    }).catch(() => fail('session_create_transport_failed'));
    if (!response.ok) fail(`session_create_http_${response.status}`);
    session = await response.json().catch(() => fail('session_create_invalid_json'));
    if (!/^[a-f0-9]{32}$/.test(session.id) || !/^[A-Za-z0-9_-]{32,128}$/.test(session.token)) fail('session_create_invalid_capability');
    evidence.latency_ms.session_create = round(elapsed());
    const url = sessionURL(''); url.protocol = 'wss:';
    socket = new WebSocket(url);
    socket.addEventListener('error', () => markFailure('websocket_transport_error'));
    socket.addEventListener('close', event => {
      evidence.end.socket_closed = true; evidence.end.close_code = event.code;
      if (!closing) markFailure('unexpected_websocket_close');
    });
    socket.addEventListener('message', event => {
      const messageReceivedAt = performance.now();
      try {
        if (typeof event.data !== 'string' || event.data.length > 100000) fail('unexpected_server_frame');
        const message = JSON.parse(event.data);
        if (message.type === 'error') {
          if (privateErrors.length < 8 && typeof message.message === 'string') {
            let detail = message.message;
            // Preserve bounded provider diagnostics privately, without retaining
            // session identifiers and capabilities even if echoed upstream.
            for (const secret of [session?.token, session?.id]) {
              if (secret) detail = detail.replaceAll(secret, '[redacted]');
            }
            privateErrors.push({ elapsed_ms: round(elapsed()), message: detail.slice(0, 4096), truncated: detail.length > 4096 });
          }
          markFailure('server_reported_error'); return;
        }
        if (message.type === 'ended') { evidence.end.acknowledged = true; return; }
        if (closing) return;
        if (opt.expectSingleUserTurn && isPrematureReply(message, messageReceivedAt, inputDoneAt)) {
          evidence.pause_acceptance.premature_reply_observed = true;
          evidence.pause_acceptance.first_premature_reply_from_start_ms ??= round(messageReceivedAt - started);
          markFailure('premature_reply_before_input_end');
        }
        if (message.type === 'ready') { ready = true; evidence.latency_ms.ready = round(elapsed()); }
        if (message.type === 'reset' || message.type === 'clear') {
          if (!Number.isSafeInteger(message.generation)) fail('invalid_generation');
          if (message.generation < generation) return;
          generation = Math.max(generation, message.generation); queue.length = 0; scheduledEnd = performance.now();
        }
        if (message.type === 'transcript' && message.final === true) {
          if (!['user', 'assistant'].includes(message.role) || typeof message.text !== 'string' || !message.text.trim() || message.text.length > 12000) fail('invalid_transcript');
          if (transcripts.length >= 30) fail('transcript_bound_exceeded');
          const transcriptAt = messageReceivedAt;
          transcripts.push({ elapsed_ms: round(transcriptAt - started), role: message.role, text: message.text });
          if (message.role === 'user') {
            evidence.transcript.user_final_count++; userFinalAt = transcriptAt;
            if (opt.expectSingleUserTurn && evidence.transcript.user_final_count > 1) markFailure('unexpected_user_turn_split');
          } else evidence.transcript.assistant_sentence_count++;
        }
        if (message.type === 'audio') {
          if (message.sample_rate !== OUTPUT_RATE || !Number.isSafeInteger(message.generation) || !Number.isSafeInteger(message.chunk_id) || typeof message.data !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/.test(message.data)) fail('invalid_audio_packet');
          if (message.generation < generation) return;
          if (message.generation > generation) { queue.length = 0; scheduledEnd = performance.now(); generation = message.generation; }
          const key = `${message.generation}:${message.chunk_id}`;
          if (seen.has(key)) fail('duplicate_audio_packet');
          seen.add(key);
          const pcm = Buffer.from(message.data, 'base64');
          if (!pcm.length || pcm.length % 2 || pcm.length > 4800 || pcm.toString('base64') !== message.data) fail('invalid_pcm_output');
          totalOutputBytes += pcm.length;
          if (totalOutputBytes > OUTPUT_RATE * 2 * 30) fail('output_capture_bound_exceeded');
          const now = messageReceivedAt, duration = pcm.length / (OUTPUT_RATE * 2) * 1000;
          scheduledEnd = Math.max(now, scheduledEnd) + duration;
          if (scheduledEnd - now > 12000) fail('playback_queue_bound_exceeded');
          if (firstAudioAt === undefined) {
            firstAudioAt = now;
            userFinalBeforeFirstAudioAt = userFinalAt;
          }
          queue.push({ generation, chunk_id: message.chunk_id, due: scheduledEnd, duration, received_at: now, sentence: Boolean(message.text?.trim()) });
          audio.push(pcm); evidence.playback.received_chunks++;
        }
      } catch (error) { markFailure(error instanceof Failure ? error.code : 'server_message_parse_failed'); }
    });
    receiptTimer = setInterval(() => {
      if (closing || fatal) return;
      try {
        while (queue.length && queue[0].due <= performance.now()) {
          const item = queue.shift();
          if (item.generation !== generation) continue;
          send({ type: 'played', generation: item.generation, chunk_id: item.chunk_id });
          receiptTrace.push({ generation: item.generation, chunk_id: item.chunk_id, duration_ms: round(item.duration),
            received_elapsed_ms: round(item.received_at - started), due_elapsed_ms: round(item.due - started), receipt_elapsed_ms: round(elapsed()) });
          evidence.playback.acknowledged_chunks++;
          evidence.playback.acknowledged_duration_ms += item.duration;
          if (item.sentence) { evidence.playback.completed_sentences++; break; }
        }
      } catch (error) { markFailure(error instanceof Failure ? error.code : 'receipt_send_failed'); }
    }, 10);
    heartbeat = setInterval(() => { if (!closing && socket?.readyState === WebSocket.OPEN) { try { send({ type: 'ping' }); } catch { markFailure('heartbeat_failed'); } } }, 5000);
    await waitFor(() => ready, 30000, 'ready_timeout');
    const inputStart = performance.now();
    evidence.latency_ms.input_start = round(inputStart - started);
    inputTask = (async () => {
      let offset = 0;
      while (!stopInput && !fatal) {
        const now = performance.now();
        if (now - inputStart > 55000) { markFailure('response_timeout'); break; }
        const pcm = Buffer.alloc(INPUT_RATE * 2 * CHUNK_MS / 1000);
        const count = Math.min(pcm.length, input.length - offset);
        if (count > 0) { input.copy(pcm, 0, offset, offset + count); offset += count; }
        send({ type: 'audio', sample_rate: INPUT_RATE, data: pcm.toString('base64') });
        // Continue real-time silence so Flux can detect the end of the utterance.
        if (offset >= input.length) inputDoneAt ??= performance.now() + count / 32;
        await sleep(CHUNK_MS);
      }
    })().catch(error => markFailure(error instanceof Failure ? error.code : 'input_send_failed'));
    await waitFor(() => evidence.playback.completed_sentences > 0, 55000, 'response_timeout');
    if (!evidence.transcript.user_final_count || !evidence.transcript.assistant_sentence_count || !firstAudioAt) fail('missing_real_turn_evidence');
    if (opt.expectSingleUserTurn && evidence.transcript.user_final_count !== 1) fail('unexpected_user_turn_split');
    if (!pcmStats(Buffer.concat(audio)).nonzero_samples) fail('provider_output_is_silent');
    evidence.stop_reason = 'first_complete_sentence_received_and_elapsed_playback_receipts_sent';
  } catch (error) {
    evidence.failures.push(error instanceof Failure ? error.code : 'unexpected_runtime_failure');
  } finally {
    closing = true; stopInput = true; clearInterval(receiptTimer); clearInterval(heartbeat); queue.length = 0;
    if (inputTask) await inputTask;
    if (socket?.readyState === WebSocket.OPEN) {
      try { send({ type: 'end' }); evidence.end.sent = true; }
      catch { evidence.failures.push('end_send_failed'); }
      try { await waitFor(() => socket.readyState === WebSocket.CLOSED, 10000, 'end_close_timeout', false); }
      catch { evidence.failures.push('end_close_timeout'); }
    }
    if (socket && socket.readyState !== WebSocket.CLOSED) { try { socket.close(1000, 'Smoke test complete'); } catch {} }
    if (session?.id && session?.token) {
      const deadline = performance.now() + 15000;
      do {
        try {
          const r = await fetch(sessionURL('/diagnostics'), { redirect: 'error', signal: AbortSignal.timeout(4000) });
          if (!r.ok) fail('diagnostics_request_failed');
          evidence.cleanup = safeDiagnostics(await r.json());
          if (evidence.cleanup.closed && RESOURCE_KEYS.every(key => evidence.cleanup[key] === 0)) break;
        } catch { evidence.cleanup_observation_failed = true; }
        await sleep(500);
      } while (performance.now() < deadline);
      evidence.cleanup_verified = evidence.cleanup?.closed === true && RESOURCE_KEYS.every(key => evidence.cleanup[key] === 0);
      if (!evidence.cleanup_verified) evidence.failures.push('cleanup_not_verified');
    }
    process.removeListener('SIGINT', interrupted); process.removeListener('SIGTERM', interrupted);
    if (!evidence.end.sent || !evidence.end.acknowledged || !evidence.end.socket_closed || evidence.end.close_code !== 1000) evidence.failures.push('normal_end_not_verified');
    if (firstAudioAt !== undefined) {
      evidence.latency_ms.first_audio_from_start = round(firstAudioAt - started);
      if (userFinalBeforeFirstAudioAt !== undefined) evidence.latency_ms.first_audio_after_final_transcript = round(firstAudioAt - userFinalBeforeFirstAudioAt);
      if (inputDoneAt !== undefined) evidence.latency_ms.first_audio_after_input_end = round(firstAudioAt - inputDoneAt);
    }
    evidence.playback.acknowledged_duration_ms = round(evidence.playback.acknowledged_duration_ms);
    evidence.playback.received_audio_duration_ms = round(totalOutputBytes / 48);
    evidence.playback.output_statistics = pcmStats(Buffer.concat(audio));
    evidence.finished_at = new Date().toISOString();
    evidence.elapsed_ms = round(elapsed());
    // A provider/transport error can arrive after the first sentence completes
    // while End is in flight. It must still make the smoke result fail.
    if (fatal && !evidence.failures.includes(fatal.code)) evidence.failures.push(fatal.code);
    if (opt.expectSingleUserTurn && evidence.transcript.user_final_count > 1 && !evidence.failures.includes('unexpected_user_turn_split')) evidence.failures.push('unexpected_user_turn_split');
    if (inputDoneAt !== undefined) evidence.latency_ms.input_finish = round(inputDoneAt - started);
    evidence.passed = evidence.failures.length === 0 && evidence.playback.completed_sentences > 0 && evidence.cleanup_verified;
    if (opt.expectSingleUserTurn) {
      evidence.pause_acceptance.passed = evidence.passed && evidence.transcript.user_final_count === 1
        && inputDoneAt !== undefined && !evidence.pause_acceptance.premature_reply_observed;
      evidence.passed = evidence.pause_acceptance.passed;
    }
    // Evidence never includes session objects, capability URLs, headers, server
    // error text, raw diagnostics or history; only allowlisted metadata.
    await fs.writeFile(path.join(runDir, 'transcripts.private.json'), JSON.stringify(transcripts, null, 2), { mode: 0o600 });
    await fs.writeFile(path.join(runDir, 'server-errors.private.json'), JSON.stringify(privateErrors, null, 2), { mode: 0o600 });
    await fs.writeFile(path.join(runDir, 'received-output.private.wav'), wave(Buffer.concat(audio)), { mode: 0o600 });
    await fs.writeFile(path.join(runDir, 'receipt-timing.json'), JSON.stringify(receiptTrace, null, 2), { mode: 0o600 });
    await fs.mkdir(path.dirname(evidenceFile), { recursive: true });
    await fs.writeFile(evidenceFile, JSON.stringify(evidence, null, 2) + '\n', { mode: 0o600 });
    console.log(JSON.stringify({ passed: evidence.passed, failures: evidence.failures, physical_human_voice_acceptance_passed: false,
      private_capture_directory: path.relative(process.cwd(), runDir), evidence_file: path.relative(process.cwd(), evidenceFile) }));
    if (!evidence.passed) process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main().catch(error => {
  // Exception messages/stacks can contain capability URLs: never print them.
  console.error(JSON.stringify({ passed: false, failure: error instanceof Failure ? error.code : 'local_setup_or_output_failed' }));
  process.exitCode = 1;
});
