import test from 'node:test';
import assert from 'node:assert/strict';
import { shareableServerDiagnostics, shareableBrowserErrors } from './session-measurements.mjs';

test('diagnostics export accepts known values and drops credentials, text and resource IDs',()=>{
  const report=shareableServerDiagnostics({transport:'webrtc',input_audio_bytes:1280,
    forwarded_audio_bytes:640,sfu_input_bytes:2560,sfu_input_socket_open:true,sfu_input_pcm_ready:false,sfu_input_failed:false,closed:false,
    secret:'PRIVATE',history:'PRIVATE',sfu_cleanup_records:[{sessionId:'PRIVATE'}],
    metrics:[{event:'smart_turn',elapsed_ms:125,complete:false,probability:.1,text:'PRIVATE'},
      {event:'response_failed',stage:'synthesis',exception_type:'PRIVATE'},
      {event:'turn_discarded',reason:'turn_readiness_timeout'},
      {event:'turn_discarded',reason:'PRIVATE'}, {event:'PRIVATE',text:'PRIVATE'}]});
  assert.equal(report.sfu_input_socket_open,true);
  assert.equal(report.sfu_input_pcm_ready,false);
  assert.equal(report.sfu_input_failed,false);
  assert.deepEqual(report.events,[{event:'smart_turn',elapsed_ms:125,complete:false,probability:.1},
    {event:'response_failed',stage:'synthesis'},
    {event:'turn_discarded',reason:'turn_readiness_timeout'}, {event:'turn_discarded'}]);
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE/);
});
test('malformed fields and excessive event history cannot escape the allowlist',()=>{
  const report=shareableServerDiagnostics({input_audio_bytes:'PRIVATE',forwarded_audio_bytes:-1,
    sfu_input_bytes:Infinity,closed:'PRIVATE',transport:'PRIVATE',
    metrics:Array.from({length:700},()=>({event:'speech_started',revision:'PRIVATE',generation:NaN}))});
  assert.deepEqual(Object.keys(report),['events']);
  assert.equal(report.events.length,500);
  assert.deepEqual(report.events[0],{event:'speech_started'});
  for(const raw of [null,[],false,'PRIVATE']) assert.throws(()=>shareableServerDiagnostics(raw));
});

test('missing speech onset remains identifiable in a shared report',()=>{
  assert.deepEqual(shareableServerDiagnostics({metrics:[{event:'turn_discarded',reason:'results_without_speech_start'}]}).events,
    [{event:'turn_discarded',reason:'results_without_speech_start'}]);
});

test('every generic speech-turn abort reason survives the shared report',()=>{
  const reasons = ['invalid_nova_event', 'transcript_beyond_pause', 'pause_audio_unavailable',
    'detector_busy', 'smart_turn_failed', 'turn_readiness_timeout', 'pipecat_turn_closed'];
  const metrics = reasons.map(reason => ({event:'turn_discarded',reason}));
  assert.deepEqual(shareableServerDiagnostics({metrics}).events, metrics);
});

test('abort diagnostics preserve only known reasons and numeric fields',()=>{
  const report = shareableServerDiagnostics({token:'PRIVATE_TOKEN',metrics:[{
    event:'turn_discarded',reason:'smart_turn_failed',elapsed_ms:150,revision:3,
    transcript:'PRIVATE_TRANSCRIPT',audio:'PRIVATE_AUDIO',token:'PRIVATE_TOKEN',
    message:'PRIVATE_EXCEPTION',details:{reason:'PRIVATE_NESTED',transcript:'PRIVATE_TRANSCRIPT'},
  },{event:'turn_discarded',reason:'PRIVATE_REASON',elapsed_ms:'PRIVATE_TIME',
    revision:{text:'PRIVATE_REVISION'}},{event:'turn_discarded',reason:{text:'PRIVATE_OBJECT'}}]});
  assert.deepEqual(report.events,[
    {event:'turn_discarded',elapsed_ms:150,revision:3,reason:'smart_turn_failed'},
    {event:'turn_discarded'},{event:'turn_discarded'},
  ]);
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE/);
});

test('abort history is capped at the latest 500 events',()=>{
  const metrics = Array.from({length:700},(_,revision)=>({
    event:'turn_discarded',reason:'smart_turn_failed',revision,text:'PRIVATE_TRANSCRIPT',
  }));
  const report = shareableServerDiagnostics({metrics});
  assert.equal(report.events.length,500);
  assert.equal(report.events[0].revision,200);
  assert.equal(report.events.at(-1).revision,699);
  assert.ok(report.events.every(event=>event.reason==='smart_turn_failed'));
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE/);
});

test('hosted request lifecycle remains distinct from coordinator cancellation',()=>{
  const metrics = [
    {event:'turn_provider',elapsed_ms:100,request_sequence:1,stage:'submitted',pending:true},
    {event:'turn_provider',elapsed_ms:110,request_sequence:1,stage:'wait_cancelled',pending:true,request_elapsed_ms:10},
    {event:'turn_provider',elapsed_ms:120,request_sequence:2,active_request_sequence:1,stage:'rejected_busy',pending:true},
    {event:'turn_provider',elapsed_ms:150,request_sequence:1,stage:'settled_ok',pending:false,request_elapsed_ms:50},
  ];
  assert.deepEqual(shareableServerDiagnostics({metrics}).events,metrics);
});

test('hosted request diagnostics exclude unrecognized states and private payloads',()=>{
  const metrics = [{event:'turn_provider',request_sequence:'PRIVATE',active_request_sequence:-1,
    request_elapsed_ms:Infinity,pending:'PRIVATE',stage:'PRIVATE',
    audio:'PRIVATE',parameters:{audio:'PRIVATE'},response:{transcript:'PRIVATE'},
    exception:'PRIVATE',model:'PRIVATE',token:'PRIVATE'}];
  assert.deepEqual(shareableServerDiagnostics({metrics}).events,[{event:'turn_provider'}]);
});

test('shared trace retains Nova ranges, finality, audio cursors and cancellation ordering',()=>{
  const metrics = [
    {event:'input_audio_progress',elapsed_ms:20,input_audio_chunks:5,input_audio_bytes:3200,
      forwarded_audio_bytes:3200,sfu_input_bytes:19200,sfu_dropped_packets:0,sfu_input_packets:5,
      sfu_decoded_pcm_bytes:3200,sfu_input_connections:1,sfu_last_sequence:5,
      sfu_last_packet_timestamp:4800,sfu_first_callback_ms:2,sfu_first_pcm_ms:3,
      sfu_input_socket_open:true,sfu_input_pcm_ready:true,sfu_input_failed:false},
    {event:'nova_event',elapsed_ms:30,revision:2,connection_generation:1,event_connection_generation:1,
      current_connection:true,nova_type:'Results',nova_start_secs:0,nova_duration_secs:.1,
      audio_cursor_sample:1600,buffer_start_sample:0,consumed_sample:0,onset_sample:0,
      latest_onset_sample:0,pause_end_sample:1600,active:true,speaking:false,queued:false,
      transcript_covered:true,final_segments:1,transcript_chars:8,event_transcript_chars:8,
      word_count:1,first_word_start_secs:0,last_word_end_secs:.08,is_final:true,speech_final:true},
    {event:'turn_snapshot',elapsed_ms:31,revision:2,snapshot_samples:1280,
      transcript_end_sample:1600,audio_start_sample:0,audio_end_sample:1280},
    {event:'smart_turn_request',elapsed_ms:32,revision:2,request_revision:2,snapshot_samples:1280},
    {event:'turn_cancelled',elapsed_ms:40,revision:2,next_revision:3,detector_cancelled:true,deadline_cancelled:true},
    {event:'smart_turn_outcome',elapsed_ms:41,request_revision:2,outcome:'cancelled'},
    {event:'nova_event_rejected',elapsed_ms:42,validation:'onset_beyond_audio',nova_timestamp_secs:.2},
    {event:'turn_discarded',elapsed_ms:43,reason:'stt_connection_changed'},
    {event:'generation_cancelled',elapsed_ms:44,generation:1},
    {event:'server_clear',elapsed_ms:45,generation:2,dispatch_ms:1},
  ];
  assert.deepEqual(shareableServerDiagnostics({metrics}).events,metrics);
});

test('trace export drops malformed timings, booleans, enum values and nested sensitive content',()=>{
  const report = shareableServerDiagnostics({sfu_input_packets:2,sfu_decoded_pcm_bytes:1280,
    sfu_last_sequence:NaN,sfu_first_pcm_ms:'PRIVATE',metrics:[{
      event:'nova_event',nova_type:'PRIVATE',validation:'PRIVATE',outcome:'PRIVATE',
      audio_cursor_sample:-1,nova_start_secs:'PRIVATE',nova_duration_secs:Infinity,
      last_word_end_secs:NaN,is_final:'PRIVATE',speech_final:1,active:{token:'PRIVATE'},
      current_connection:[],transcript:'PRIVATE',audio:'PRIVATE',error:'PRIVATE',
      channel:{alternatives:[{transcript:'PRIVATE',words:[{word:'PRIVATE'}]}]},
    },{event:'smart_turn_outcome',outcome:'incomplete',complete:false,
      response:{transcript:'PRIVATE'},pcm:[1,2],token:'PRIVATE'}]});
  assert.deepEqual(report,{sfu_input_packets:2,sfu_decoded_pcm_bytes:1280,events:[
    {event:'nova_event'},{event:'smart_turn_outcome',complete:false,outcome:'incomplete'},
  ]});
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE/);
});

test('invalid negative provider timestamps remain observable without admitting unsafe numeric values',()=>{
  const timings = {nova_timestamp_secs:-.1,nova_start_secs:-.2,nova_duration_secs:-.3,
    first_word_start_secs:-.4,last_word_end_secs:-.5};
  const words = {word_index:0,word_start_sample:1,word_end_sample:2,word_previous_sample:3};
  const report = shareableServerDiagnostics({input_audio_bytes:Number.MAX_SAFE_INTEGER+1,metrics:[
    {event:'nova_event_rejected',validation:'timestamp',...timings,...words,
      elapsed_ms:-1,audio_cursor_sample:-1,snapshot_samples:Number.MAX_SAFE_INTEGER+1},
    {event:'nova_event',nova_timestamp_secs:NaN,nova_start_secs:Infinity,nova_duration_secs:-Infinity,
      first_word_start_secs:Number.MAX_SAFE_INTEGER+1,last_word_end_secs:-Number.MAX_SAFE_INTEGER-1},
  ]});
  assert.deepEqual(report,{events:[
    {event:'nova_event_rejected',...timings,...words,validation:'timestamp'},
    {event:'nova_event'},
  ]});
});

test('browser errors export a fixed abort code and normalized timestamp without free text',()=>{
  const report = shareableBrowserErrors([
    {at:'2026-10-05T20:29:45.032Z',message:'Speech turn could not be completed; please repeat your request.',
      token:'PRIVATE',details:{audio:'PRIVATE'}},
    {at:'2026-10-05T20:29:46.000Z',message:'PRIVATE transcript or token-bearing URL'},
    {at:'PRIVATE',message:'PRIVATE'},
    {at:'2026-02-30T20:29:45.032Z',message:'PRIVATE'},
    {at:'2026-10-05T20:29:45.032Z PRIVATE',message:{transcript:'PRIVATE'}},
    null,[],false,'PRIVATE',
  ]);
  assert.deepEqual(report,[
    {code:'turn_abort',at:'2026-10-05T20:29:45.032Z'},
    {code:'redacted',at:'2026-10-05T20:29:46.000Z'},
    {code:'redacted'},{code:'redacted'},{code:'redacted'},
  ]);
  assert.doesNotMatch(JSON.stringify(report),/PRIVATE|transcript|token|audio|message/);
});

test('browser error export retains only the latest 1000 records and tolerates absent input',()=>{
  const errors = Array.from({length:1200},(_,index)=>({
    message:index<200?'Speech turn could not be completed; please repeat your request.':'PRIVATE',
  }));
  const report = shareableBrowserErrors(errors);
  assert.equal(report.length,1000);
  assert.ok(report.every(error=>error.code==='redacted'));
  for (const raw of [undefined,null,{},'PRIVATE',false]) assert.deepEqual(shareableBrowserErrors(raw),[]);
});
