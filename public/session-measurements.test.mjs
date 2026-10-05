import test from 'node:test';
import assert from 'node:assert/strict';
import { shareableServerDiagnostics } from './session-measurements.mjs';

test('diagnostics export accepts known values and drops credentials, text and resource IDs',()=>{
  const report=shareableServerDiagnostics({transport:'webrtc',input_audio_bytes:1280,
    forwarded_audio_bytes:640,sfu_input_bytes:2560,sfu_input_ready:true,closed:false,
    secret:'PRIVATE',history:'PRIVATE',sfu_cleanup_records:[{sessionId:'PRIVATE'}],
    metrics:[{event:'smart_turn',elapsed_ms:125,complete:false,probability:.1,text:'PRIVATE'},
      {event:'response_failed',stage:'synthesis',exception_type:'PRIVATE'},
      {event:'turn_discarded',reason:'turn_readiness_timeout'},
      {event:'turn_discarded',reason:'PRIVATE'}, {event:'PRIVATE',text:'PRIVATE'}]});
  assert.equal(report.sfu_input_ready,true);
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
