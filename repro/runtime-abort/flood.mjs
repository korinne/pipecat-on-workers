import fs from 'node:fs/promises';
const base=process.argv[2]||'http://127.0.0.1:8791';
const seconds=Number(process.argv[3]||30), burst=Number(process.argv[4]||20);
const abortMode=process.argv.includes('--abort-js')?'abort-js':process.argv.includes('--abort')?'abort':null;
const abort=Boolean(abortMode);
const name=`test-${Date.now()}`;
const result={scope:'Four minimal Python Durable Objects, no Pipecat/providers; WebSocket JSON/base64 decode only',seconds,burst_per_20ms:burst,controlled_abort:abort,abort_strategy:abortMode,errors:[],calls:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const wait=async(p,label,ms=15000)=>{const end=Date.now()+ms;while(!p()){if(Date.now()>end)throw Error('Timeout: '+label);await sleep(10);}};
const calls=Array.from({length:4},(_,i)=>({name:`${name}-${i}`,events:[],sent:0}));
for(const c of calls){
  c.ws=new WebSocket(`${base.replace(/^http/,'ws')}/${c.name}`);
  c.ws.onmessage=e=>{const v=JSON.parse(e.data);c.events.push(v);if(v.type==='error')result.errors.push(v.message);};
  c.ws.onclose=e=>{c.close={code:e.code,reason:e.reason};};
}
let timer;
try{
  await wait(()=>calls.every(c=>c.events.some(e=>e.type==='ready')),'ready');
  const payload=JSON.stringify({type:'audio',data:Buffer.alloc(640).toString('base64'),sample_rate:16000});
  timer=setInterval(()=>{for(const c of calls)if(c.ws.readyState===1)for(let n=0;n<burst;n++){c.ws.send(payload);c.sent++;}},20);
  const started=Date.now();
  if(abort){
    await sleep(1000);
    const response=await fetch(`${base}/${name}-separate/${abortMode}`,{method:'POST'}).catch(e=>({status:String(e)}));
    result.abort_response=response.status;
  }
  while(Date.now()-started<seconds*1000){
    if(calls.some(c=>c.ws.readyState===3))throw Error('Socket closed before experiment ended');
    await sleep(100);
  }
  clearInterval(timer);
  for(const c of calls)c.ws.send(JSON.stringify({type:'end'}));
  await wait(()=>calls.every(c=>c.ws.readyState===3),'close',30000);
  result.elapsed_seconds=(Date.now()-started)/1000;
  result.calls=calls.map(c=>({sent:c.sent,closed:c.close,last:c.events.at(-1)}));
  result.passed=result.errors.length===0&&result.calls.every(c=>c.last?.type==='ended'&&c.last.messages===c.sent&&c.last.errors===0);
}catch(e){result.errors.push(String(e));result.passed=false;result.calls=calls.map(c=>({sent:c.sent,closed:c.close,last:c.events.at(-1)}));}
finally{clearInterval(timer);for(const c of calls)if(c.ws.readyState===1)c.ws.close();}
await fs.writeFile(new URL(`./result-${abortMode||'baseline'}-${seconds}s-${burst}burst.json`,import.meta.url),JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));
if(!result.passed)process.exitCode=1;
