// Export only known counters and event fields from the private diagnostic API.
// That API also contains internal resource IDs; never copy whole objects.
const counters = [
  'generation', 'input_audio_chunks', 'input_audio_bytes', 'forwarded_audio_bytes',
  'dropped_audio_bytes', 'queued_audio_bytes', 'pending_turn_tasks',
  'pending_user_fragments', 'pending_user_chars', 'turn_audio_bytes', 'turn_revision',
  'sfu_input_bytes', 'sfu_dropped_packets', 'sfu_submitted_bytes',
];
const events = new Set(['startup', 'speech_started', 'turn_pause', 'smart_turn',
  'user_end', 'turn_discarded', 'provider_error', 'generation_started',
  'first_audio_submitted', 'response_failed', 'server_clear', 'closed']);
const numbers = ['elapsed_ms', 'revision', 'generation', 'probability',
  'snapshot_samples', 'response_ms', 'startup_ms'];
const enums = {
  stage: new Set(['generation', 'synthesis', 'output', 'output_completion']),
  provider: new Set(['nova', 'stt', 'smart_turn', 'llm', 'tts']),
  reason: new Set(['turn_readiness_timeout', 'invalid_nova_event', 'results_without_speech_start',
    'provider_reconnect', 'provider_error', 'pipecat_turn_closed', 'closed', 'ended']),
};
export function shareableServerDiagnostics(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid diagnostics');
  const result = {};
  for (const key of counters) if (Number.isFinite(raw[key]) && raw[key] >= 0) result[key] = raw[key];
  for (const key of ['closed', 'sfu_input_socket_open', 'sfu_input_pcm_ready', 'sfu_input_failed']) if (typeof raw[key] === 'boolean') result[key] = raw[key];
  if (['websocket', 'webrtc'].includes(raw.transport)) result.transport = raw.transport;
  result.events = (Array.isArray(raw.metrics) ? raw.metrics.slice(-500) : []).flatMap(item => {
    if (!item || !events.has(item.event)) return [];
    const event = { event: item.event };
    for (const key of numbers) if (Number.isFinite(item[key]) && item[key] >= 0) event[key] = item[key];
    for (const key of ['complete', 'recoverable']) if (typeof item[key] === 'boolean') event[key] = item[key];
    for (const [key, values] of Object.entries(enums)) if (values.has(item[key])) event[key] = item[key];
    return [event];
  });
  return result;
}
