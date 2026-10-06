#!/usr/bin/env node
// A real-provider connection with no client traffic must time out and clean up.
// No microphone, speaker, recording, or synthetic provider event is used.
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { safeDiagnostics } from './check_real_voice.mjs';
const keys = ['pipecat_tasks','provider_tasks','provider_sockets','provider_readers','pending_provider_requests','queued_provider_bytes','unacked_audio_bytes','pending_playback_chunks','pending_turn_tasks','pending_user_fragments','pending_user_chars'];
const base = process.argv[2], output = process.argv[3], privateOutput = process.argv[4];
const privateErrors = [];
const outputsRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
if(privateOutput && !path.relative(outputsRoot,path.resolve(privateOutput)).startsWith('..'+path.sep))throw Error('Private capture must be outside outputs');
let ws, session;
const result = { started_at:new Date().toISOString(), passed:false, fixture_provider_requested:false, client_audio_sent:false };
try {
  const origin = new URL(base);
  if(origin.protocol !== 'https:' || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/' || !output) throw Error('invalid_setup');
  const r = await fetch(new URL('/api/session',origin),{method:'POST',redirect:'error',signal:AbortSignal.timeout(10000)});
  if(!r.ok) throw Error('session_create_failed');
  session=await r.json();
  if(!/^[a-f0-9]{32}$/.test(session.id) || !/^[A-Za-z0-9_-]{32,128}$/.test(session.token)) throw Error('invalid_session');
  const url = suffix => { const u=new URL(`/api/session/${session.id}${suffix}`,origin);u.searchParams.set('token',session.token);return u; };
  const u=url('');u.protocol='wss:';
  const began=performance.now();let readyAt;
  await new Promise((resolve,reject)=>{
    ws=new WebSocket(u);
    const timer=setTimeout(()=>{reject(Error('abandon_timeout_not_observed'));ws.close();},45000);
    ws.addEventListener('message',e=>{try{const m=JSON.parse(e.data);if(m.type==='ready')readyAt=performance.now();if(m.type==='error' && privateErrors.length<8){let text=String(m.message).slice(0,4000);for(const key of [session.token,session.id])text=text.replaceAll(key,'[redacted]');privateErrors.push(text);}}catch{clearTimeout(timer);reject(Error('invalid_message'));}});
    ws.addEventListener('error',()=>{clearTimeout(timer);reject(Error('socket_error'));});
    ws.addEventListener('close',e=>{clearTimeout(timer);result.close_code=e.code;result.abandoned_reason_matched=e.reason==='abandoned_timeout';result.ready_observed=readyAt!==undefined;result.idle_after_ready_ms=readyAt===undefined?null:Math.round(performance.now()-readyAt);result.elapsed_ms=Math.round(performance.now()-began);resolve();});
  });
  const d=await fetch(url('/diagnostics'),{redirect:'error',signal:AbortSignal.timeout(5000)});
  if(!d.ok)throw Error('diagnostics_failed');
  result.cleanup=safeDiagnostics(await d.json());
  result.passed=result.ready_observed && result.abandoned_reason_matched && result.close_code===1012 && result.idle_after_ready_ms>=29000 && result.cleanup.closed && keys.every(k=>result.cleanup[k]===0);
  if(!result.passed)result.failure='abandon_or_cleanup_not_verified';
} catch(e) { result.failure=/^[a-z_]+$/.test(e.message)?e.message:'request_failed'; }
finally { result.provider_errors_observed=privateErrors.length;if(privateOutput)await fs.writeFile(privateOutput,JSON.stringify(privateErrors,null,2),{mode:0o600}); if(ws && ws.readyState!==WebSocket.CLOSED)ws.close(); if(output)await fs.writeFile(output,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result));if(!result.passed)process.exitCode=1; }
