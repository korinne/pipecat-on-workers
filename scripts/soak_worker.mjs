/** Real local Python Durable Object + WebSocket lifecycle, synthetic providers. */
import fs from 'node:fs/promises';
const base = process.argv[2] || 'http://127.0.0.1:8787';
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const wait = async (test,label,ms=10000)=>{const end=Date.now()+ms;while(!test()){if(Date.now()>end)throw Error(`Timeout: ${label}`);await sleep(10);}};
async function create(){const r=await fetch(base+'/api/session',{method:'POST'});if(!r.ok)throw Error('Session create '+r.status);return r.json();}
function url(s,suffix=''){return `${base}/api/session/${s.id}${suffix}?token=${encodeURIComponent(s.token)}`;}
async function connect(s){
 const events=[];const ws=new WebSocket(url(s).replace(/^http/,'ws')+'&fixture=1');const start=performance.now();
 ws.onmessage=e=>{const v=JSON.parse(e.data);events.push(v);if(v.type==='audio' && c.ack) ws.send(JSON.stringify({type:'played',generation:v.generation,chunk_id:v.chunk_id}));};
 const c={ws,events,ack:true,audioCursor:0,start,send:x=>{if(x.type==='audio')c.audioCursor+=Buffer.from(x.data,'base64').length/32000;ws.send(JSON.stringify(x));}};
 ws.onclose=e=>{c.closed={code:e.code,reason:e.reason,elapsed_since_connect_seconds:(performance.now()-start)/1000};};
 await wait(()=>events.some(e=>e.type==='ready'),'ready');c.startupMs=performance.now()-start;return c;
}
async function turn(c,text,n){
 const clears=c.events.filter(e=>e.type==='clear').length;
 const start=c.audioCursor;
 c.send({type:'fixture_event',event:{type:'SpeechStarted',timestamp:start,connection_generation:1}});
 c.send({type:'audio',data:Buffer.alloc(16000).toString('base64'),sample_rate:16000});
 await wait(()=>c.events.filter(e=>e.type==='clear').length>clears,'turn clear');
 c.send({type:'fixture_event',event:{type:'Results',start,duration:c.audioCursor-start,is_final:true,speech_final:true,channel:{alternatives:[{transcript:text}]},connection_generation:1}});
 await wait(()=>c.events.some(e=>e.type==='audio'&&e.text.includes(text)),'reply');
 await sleep(20);
}
async function diagnostics(s){const r=await fetch(url(s,'/diagnostics'),{signal:AbortSignal.timeout(10000)});if(!r.ok)throw Error('Diagnostics HTTP '+r.status);return r.json();}

const duration=Number(process.argv[3]||600);
const output=new URL(process.argv[4]||'../evidence/workerd-soak.json',import.meta.url);
const result={scope:'Four actual local Python Durable Objects; WebSocket PCM and synthetic provider events; silent fixture output, not real voice',started_at:new Date().toISOString(),requested_seconds:duration,samples:[],voice_tested:false,errors:[]};
const sessions=await Promise.all(Array.from({length:4},create));
const calls=await Promise.all(sessions.map(connect));
result.startup_ms=calls.map(c=>c.startupMs);
const started=performance.now();let turns=0,completedTurns=0,interruptions=0,lastSample=-60;
const silence=Buffer.alloc(640).toString('base64');
const capture=setInterval(()=>{for(const c of calls)if(c.ws.readyState===1)c.send({type:'audio',data:silence,sample_rate:16000});},20);
try {
 while(performance.now()-started<duration*1000){
  const index=++turns;
  await Promise.all(calls.map(async(c,i)=>{
   c.ack=index%3!==0;
   const text=`session_${i}_turn_${index}`;
   await turn(c,text,index);
   const audio=c.events.filter(e=>e.type==='audio');
   if(audio.some(e=>!e.text.includes(sessions[i].id)&&e.text))throw Error('Cross-session output');
   if(!c.ack){const n=c.events.filter(e=>e.type==='clear').length;c.send({type:'interrupt'});await wait(()=>c.events.filter(e=>e.type==='clear').length>n,'barge-in clear');for(const a of audio)c.send({type:'played',generation:a.generation,chunk_id:a.chunk_id});interruptions++;}
   if(c.events.some(e=>e.type==='error'))throw Error('Server reported error');
   c.events.length=0;
   completedTurns++;
  }));
  const elapsed=(performance.now()-started)/1000;
  if(elapsed-lastSample>=59 || elapsed>duration-5){lastSample=elapsed;const ds=await Promise.all(sessions.map(diagnostics));const sample={elapsed_seconds:elapsed,turns:turns*4,interruptions,pipecat_tasks:ds.reduce((n,d)=>n+d.pipecat_tasks,0),unacked_bytes:ds.reduce((n,d)=>n+d.unacked_audio_bytes,0),context_messages:ds.map(d=>d.messages)};result.samples.push(sample);console.log(JSON.stringify(sample));}
  await sleep(Math.min(4800,Math.max(0,duration*1000-(performance.now()-started))));
 }
} catch(e) {result.errors.push(String(e));} finally {
 result.elapsed_seconds=(performance.now()-started)/1000;result.turns=completedTurns;result.attempted_turns=turns*4;result.interruptions=interruptions;
 clearInterval(capture);
 try {for(const c of calls)if(c.ws.readyState===1)c.send({type:'end'});await wait(()=>calls.every(c=>c.ws.readyState===3),'all ended');}
 catch(e){result.errors.push('Cleanup: '+String(e));for(const c of calls)c.ws.close();}
 await sleep(100);result.socket_closes=calls.map(c=>c.closed||null);
 const cleanup=await Promise.allSettled(sessions.map(diagnostics));
 result.cleanup=cleanup.map(r=>r.status==='fulfilled'?r.value:{diagnostics_failed:String(r.reason)});
 result.passed=result.errors.length===0 && result.elapsed_seconds>=duration && result.cleanup.every(d=>d.pipecat_tasks===0 && d.unacked_audio_bytes===0);
 result.finished_at=new Date().toISOString();
 await fs.writeFile(output,JSON.stringify(result,null,2));
}
console.log(JSON.stringify({passed:result.passed,elapsed_seconds:result.elapsed_seconds,turns:result.turns,interruptions:result.interruptions}));
if(!result.passed)process.exitCode=1;
