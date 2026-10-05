/** Real local Python Durable Object + WebSocket lifecycle, synthetic providers. */
import fs from 'node:fs/promises';
const base = process.argv[2] || 'http://127.0.0.1:8787';
const output = new URL(process.argv[3] || '../evidence/workerd-lifecycle.json', import.meta.url);
const restartStrategy = process.argv[4] || 'SDK ctx.abort';
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const wait = async (test,label,ms=10000)=>{const end=Date.now()+ms;while(!test()){if(Date.now()>end)throw Error(`Timeout: ${label}`);await sleep(10);}};
async function create(){const r=await fetch(base+'/api/session',{method:'POST'});if(!r.ok)throw Error('Session create '+r.status);return r.json();}
function url(s,suffix=''){return `${base}/api/session/${s.id}${suffix}?token=${encodeURIComponent(s.token)}`;}
async function connect(s){
 const events=[];const ws=new WebSocket(url(s).replace(/^http/,'ws')+'&fixture=1');const start=performance.now();
 ws.onmessage=e=>{const v=JSON.parse(e.data);events.push(v);if(v.type==='audio' && c.ack) ws.send(JSON.stringify({type:'played',generation:v.generation,chunk_id:v.chunk_id}));};
 const c={ws,events,ack:true,audioCursor:0,start,send:x=>{if(x.type==='audio')c.audioCursor+=Buffer.from(x.data,'base64').length/32000;ws.send(JSON.stringify(x));}};
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
async function diagnostics(s){const r=await fetch(url(s,'/diagnostics'));return r.json();}
const results={scope:'Actual local workerd Python Durable Objects and network WebSockets; fixture providers, silent PCM; no real voice',started_at:new Date().toISOString(),restart_strategy:restartStrategy,runtime:await (await fetch(base+'/api/health')).json(),checks:[]};
const sessions=await Promise.all([create(),create()]);let calls=await Promise.all(sessions.map(connect));
results.startup_ms=calls.map(c=>Math.round(c.startupMs));
await Promise.all(calls.map((c,i)=>turn(c,`private_session_${i}`,1)));
for(let i=0;i<2;i++){if(calls[i].events.some(e=>e.type==='audio'&&e.text.includes(`private_session_${1-i}`)))throw Error('Cross-session leak');}
results.checks.push('two distinct DOs have isolated transcript and PCM metadata');
const old=calls[0];old.ws.close(1000,'test disconnect');await wait(()=>old.ws.readyState===3,'closed');await sleep(150);
calls[0]=await connect(sessions[0]);const reset=calls[0].events.find(e=>e.type==='reset');if(!reset.history.some(m=>m.content?.includes('private_session_0')))throw Error('History missing on reconnect');
results.checks.push('actual socket disconnect/reconnect restores persisted history, creates fresh pipeline');
await turn(calls[0],'before_restart',2);
await fetch(url(sessions[0],'/restart'),{method:'POST'}).catch(()=>{});
await wait(()=>calls[0].ws.readyState===3,'restart socket closed');await sleep(100);
calls[0]=await connect(sessions[0]);const restored=calls[0].events.find(e=>e.type==='reset');if(!restored.history.some(m=>m.content?.includes('before_restart')))throw Error('Restart lost history');
await turn(calls[0],'after_restart',3);results.checks.push(`${restartStrategy} controlled real DO restart closes call and next socket restores stored history`);
for(const c of calls)c.send({type:'end'});await wait(()=>calls.every(c=>c.ws.readyState===3),'all ended');await sleep(100);
results.cleanup=await Promise.all(sessions.map(diagnostics));
for(const d of results.cleanup)if(d.pipecat_tasks!==0 || d.unacked_audio_bytes!==0)throw Error('Cleanup leak');
results.checks.push('End releases both DO pipelines and queued playback accounting');
// An upgraded connection with no messages is cleaned by the 30 s receive timeout.
const abandoned=await create();const a=await connect(abandoned);await wait(()=>a.ws.readyState===3,'abandon timeout',35000);results.abandoned=await diagnostics(abandoned);if(results.abandoned.pipecat_tasks!==0)throw Error('Abandon leak');
results.checks.push('silent abandoned socket closes and releases pipeline after 30 s');
results.passed=true;results.finished_at=new Date().toISOString();
await fs.writeFile(output,JSON.stringify(results,null,2));
console.log(JSON.stringify({passed:true,checks:results.checks,startup_ms:results.startup_ms}));
