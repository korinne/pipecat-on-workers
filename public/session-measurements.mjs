// Export only known counters and event fields from the private diagnostic API.
// That API also contains internal resource IDs; never copy whole objects.
const counters = [
  'generation', 'input_audio_chunks', 'input_audio_bytes', 'forwarded_audio_bytes',
  'dropped_audio_bytes', 'queued_audio_bytes', 'pending_turn_tasks',
  'pending_user_fragments', 'pending_user_chars', 'turn_audio_bytes', 'turn_revision',
  'sfu_input_bytes', 'sfu_dropped_packets', 'sfu_submitted_bytes',
  'sfu_input_packets', 'sfu_decoded_pcm_bytes', 'sfu_input_connections',
  'sfu_last_sequence', 'sfu_last_packet_timestamp', 'sfu_first_callback_ms', 'sfu_first_pcm_ms',
];
const events = new Set(['startup', 'speech_started', 'turn_pause', 'smart_turn',
  'user_end', 'turn_discarded', 'provider_error', 'generation_started',
  'first_audio_submitted', 'response_failed', 'server_clear', 'closed', 'turn_provider',
  'nova_event', 'nova_event_rejected', 'turn_cancelled', 'turn_snapshot',
  'smart_turn_request', 'smart_turn_outcome', 'input_audio_progress', 'generation_cancelled']);
const numbers = ['elapsed_ms', 'revision', 'generation', 'probability',
  'snapshot_samples', 'response_ms', 'startup_ms', 'request_sequence',
  'active_request_sequence', 'request_elapsed_ms', 'connection_generation',
  'event_connection_generation', 'audio_cursor_sample', 'buffer_start_sample',
  'consumed_sample', 'onset_sample', 'latest_onset_sample', 'pause_end_sample',
  'final_segments', 'transcript_chars', 'event_transcript_chars', 'word_count',
  'nova_timestamp_secs', 'nova_start_secs', 'nova_duration_secs', 'first_word_start_secs',
  'last_word_end_secs', 'next_revision', 'transcript_end_sample', 'audio_start_sample',
  'audio_end_sample', 'request_revision', 'dispatch_ms', 'input_audio_chunks',
  'word_index', 'word_start_sample', 'word_end_sample', 'word_previous_sample',
  'input_audio_bytes', 'forwarded_audio_bytes', 'sfu_input_bytes', 'sfu_dropped_packets',
  'sfu_input_packets', 'sfu_decoded_pcm_bytes', 'sfu_input_connections',
  'sfu_last_sequence', 'sfu_last_packet_timestamp', 'sfu_first_callback_ms', 'sfu_first_pcm_ms'];
const signedTimings = new Set(['nova_timestamp_secs', 'nova_start_secs', 'nova_duration_secs',
  'first_word_start_secs', 'last_word_end_secs']);
const finiteNumber = value => Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER;
const booleans = ['closed', 'sfu_input_socket_open', 'sfu_input_pcm_ready', 'sfu_input_failed'];
const eventBooleans = [...booleans, 'complete', 'recoverable', 'pending', 'active', 'speaking',
  'transcript_covered', 'queued', 'current_connection', 'is_final', 'speech_final',
  'detector_cancelled', 'deadline_cancelled'];
const enums = {
  stage: new Set(['generation', 'synthesis', 'output', 'output_completion',
    'submitted', 'settled_ok', 'settled_error', 'settled_cancelled', 'wait_timeout',
    'wait_cancelled', 'rejected_busy', 'rejected_capacity', 'submit_failed']),
  provider: new Set(['nova', 'stt', 'smart_turn', 'llm', 'tts']),
  reason: new Set(['turn_readiness_timeout', 'invalid_nova_event', 'results_without_speech_start',
    'transcript_beyond_pause', 'pause_audio_unavailable', 'detector_busy', 'smart_turn_failed',
    'provider_reconnect', 'provider_error', 'pipecat_turn_closed', 'closed', 'ended', 'stt_connection_changed']),
  nova_type: new Set(['SpeechStarted', 'Results', 'ProviderStatus']),
  validation: new Set(['timestamp', 'onset_beyond_audio', 'crosses_consumed', 'result_beyond_audio',
    'flags', 'transcript_shape', 'words_shape', 'word_timing', 'missing_word_timing',
    'conflicting_final', 'overlapping_final', 'text_limit', 'event_shape']),
  outcome: new Set(['complete', 'incomplete', 'cancelled', 'failed']),
};
export function shareableServerDiagnostics(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid diagnostics');
  const result = {};
  for (const key of counters) if (finiteNumber(raw[key]) && raw[key] >= 0) result[key] = raw[key];
  for (const key of booleans) if (typeof raw[key] === 'boolean') result[key] = raw[key];
  if (['websocket', 'webrtc'].includes(raw.transport)) result.transport = raw.transport;
  result.events = (Array.isArray(raw.metrics) ? raw.metrics.slice(-500) : []).flatMap(item => {
    if (!item || !events.has(item.event)) return [];
    const event = { event: item.event };
    for (const key of numbers) if (finiteNumber(item[key]) && (item[key] >= 0 || signedTimings.has(key))) event[key] = item[key];
    for (const key of eventBooleans) if (typeof item[key] === 'boolean') event[key] = item[key];
    for (const [key, values] of Object.entries(enums)) if (values.has(item[key])) event[key] = item[key];
    return [event];
  });
  return result;
}

export function shareableBrowserErrors(raw) {
  return (Array.isArray(raw) ? raw.slice(-1000) : []).flatMap(item => {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return [];
    const error = { code: item.message === 'Speech turn could not be completed; please repeat your request.'
      ? 'turn_abort' : 'redacted' };
    // Error messages can contain URLs or provider content. Keep only a known
    // code and the browser's normalized timestamp, never the message itself.
    if (typeof item.at === 'string' && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(item.at)
      && Number.isFinite(Date.parse(item.at)) && new Date(item.at).toISOString() === item.at) error.at = item.at;
    return [error];
  });
}
