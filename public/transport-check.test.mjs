import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness({publish,fetchSession,bounded=false,duration=3,diagnostics={}}={}) {
  const elements=new Map(), contexts=[], nodes=[], sources=[], sockets=[], transports=[], requests=[], timers=new Map();
  let timerId=0;
  class Element {
    children=[]; listeners={}; textContent=''; hidden=false; disabled=false;
    addEventListener(type,fn){this.listeners[type]=fn;} append(...children){this.children.push(...children);}
    replaceChildren(...children){this.children=children;} get lastElementChild(){return this.children.at(-1);}
    click(){return this.listeners.click?.();} pause(){} async play(){}
  }
  const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  element('check-wav').files=[{size:12,arrayBuffer:async()=>Uint8Array.from([82,73,70,70,0,0,0,0,87,65,86,69]).buffer}];
  class Node {
    gain={}; fftSize=2048; stopped=false;
    constructor(){nodes.push(this);} connect(next){return next;} disconnect(){this.disconnected=true;}
    start(){this.started=true;} stop(){this.stopped=true;} getFloatTimeDomainData(array){array.fill(.12);}
  }
  class Context {
    state='running'; currentTime=0; destination={};
    constructor(){contexts.push(this);} async resume(){this.state='running';} async close(){this.state='closed';}
    audioWorklet={addModule:async()=>{}};
    async decodeAudioData(){return {duration};}
    createMediaStreamDestination(){const node=new Node();node.track={stopped:false,stop(){this.stopped=true;}};node.stream={getTracks:()=>[node.track],getAudioTracks:()=>[node.track]};return node;}
    createOscillator(){return new Node();} createGain(){return new Node();}
    createBufferSource(){const node=new Node();sources.push(node);return node;}
    createMediaStreamSource(){return new Node();} createAnalyser(){return new Node();}
  }
  class Socket {
    static OPEN=1;readyState=0;sent=[];
    constructor(url){this.url=String(url);sockets.push(this);}open(){this.readyState=1;this.onopen?.();}
    send(data){this.sent.push(JSON.parse(data));} receive(data){this.onmessage?.({data:JSON.stringify(data)});}
    close(){this.readyState=3;this.onclose?.();}
  }
  class SfuAudioTransport {
    floor=-1;output=null;
    constructor(options){this.options=options;transports.push(this);} async publish(){if(publish)await publish();}
    async subscribe(generation){this.output={generation,pc:{getStats:async()=>new Map([['audio',{type:'inbound-rtp',kind:'audio',bytesReceived:3200,packetsReceived:8}]])}};this.options.audio.srcObject={};}
    clearOutput(generation){this.floor=Math.max(this.floor,generation??this.floor+1);this.output=null;this.options.audio.srcObject=null;}
    close(){this.closed=true;this.clearOutput();} async resumePlayback(){}
  }
  const sandbox=vm.createContext({SfuAudioTransport,document:{getElementById:element,createElement:()=>new Element()},
    ArrayBuffer,Int16Array,DataView,Blob,AudioWorkletNode:class extends Node {port={};},
    window:{AudioContext:Context,RTCPeerConnection:class {},addEventListener(){}},
    WebSocket:Socket,performance,URL,AbortSignal,Uint8Array,Float32Array,location:{href:`https://voice.example/transport-check${bounded?'?once=1&revision='+'a'.repeat(40)+'&deployment=12345678-1234-1234-1234-123456789012':''}`,protocol:'https:'},
    fetch:async(url,options)=>{requests.push({url,options});if(url==='/api/session'&&fetchSession)return fetchSession();return {ok:true,json:async()=>url==='/api/session'?{id:'test-id',token:'test-token'}:diagnostics};},
    setTimeout(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},setInterval(fn,delay){const id=++timerId;timers.set(id,{fn,delay});return id;},
    clearTimeout(id){timers.delete(id);},clearInterval(id){timers.delete(id);},
  });
  vm.runInContext(fs.readFileSync(new URL('./transport-check.mjs',import.meta.url),'utf8').replace(/^import .*\n/gm,''),sandbox);
  return {element,contexts,nodes,sources,sockets,transports,requests,timers,run:code=>vm.runInContext(code,sandbox)};
}
test('developer check publishes synthetic audio and starts the WAV only after input readiness',async()=>{
  let ready;const h=harness({publish:()=>new Promise(resolve=>{ready=resolve;})});
  await h.element('check-start').click();const ws=h.sockets[0];ws.open();ws.receive({type:'ready'});await settle();
  assert.equal(h.sources.length,0);assert.equal(h.transports.length,1);
  assert.equal(h.requests.length,1);
  assert.equal(h.requests[0].url,'/api/session');
  assert.deepEqual(Object.keys(h.requests[0].options.headers),['Content-Type']);
  assert.equal(JSON.parse(h.requests[0].options.body).transport,'webrtc');
  assert.match(ws.url,/\?token=test-token$/);
  ready();await settle();assert.equal(h.sources.length,1);assert.equal(h.sources[0].started,true);
  assert.equal(h.element('check-input-runs').textContent,'1');
  assert.equal(ws.sent.some(p=>p.type==='audio'||p.type==='played'),false);
  h.element('check-end').click();assert.equal(h.sources[0].stopped,true);assert.equal(h.transports[0].closed,true);
  assert.equal(h.contexts[0].state,'closed');assert.equal(h.timers.size,0);assert.equal(ws.sent.at(-1).type,'end');
});
test('developer check observes nonzero remote audio and RTP without playback receipts',async()=>{
  const h=harness();await h.element('check-start').click();const ws=h.sockets[0];ws.open();ws.receive({type:'ready'});await settle();
  ws.receive({type:'sfu_track',generation:2});await settle();await h.run('measure(run)');
  assert.equal(h.element('check-rtp-bytes').textContent,'3200');assert.equal(h.element('check-rtp-packets').textContent,'8');
  assert.equal(h.element('check-nonzero-windows').textContent,'1');assert.match(h.element('check-summary').textContent,/Nonzero audio received/);
  h.element('check-interrupt').click();assert.equal(h.transports[0].floor,3);assert.equal(ws.sent.at(-1).type,'interrupt');
  assert.equal(h.element('check-audio').srcObject,null);assert.equal(ws.sent.some(p=>p.type==='played'),false);
  // Simulate the recording ending, then replay it to drive the following turn.
  h.sources[0].onended();h.element('check-replay').click();assert.equal(h.sources.length,2);
  ws.receive({type:'sfu_track',generation:3});await settle();assert.equal(h.element('check-nonzero-windows').textContent,'0');
  await h.run('measure(run)');assert.equal(h.element('check-nonzero-windows').textContent,'1');
  h.element('check-end').click();
});
test('End during media negotiation prevents late WAV playback',async()=>{
  let ready;const h=harness({publish:()=>new Promise(resolve=>{ready=resolve;})});
  await h.element('check-start').click();const ws=h.sockets[0];ws.open();ws.receive({type:'ready'});await settle();
  h.element('check-end').click();ready();await settle();
  assert.equal(h.sources.length,0);assert.equal(h.transports[0].closed,true);assert.equal(h.timers.size,0);
});
test('End during session creation retires the late capability without starting audio',async()=>{
  let finish;const h=harness({fetchSession:()=>new Promise(resolve=>{finish=resolve;})});
  const start=h.element('check-start').click();await settle();h.element('check-end').click();
  finish({ok:true,json:async()=>({id:'late-id',token:'late-token'})});await start;
  assert.equal(h.sources.length,0);assert.equal(h.sockets.length,1);
  h.sockets[0].open();assert.deepEqual(h.sockets[0].sent,[{type:'end'}]);assert.equal(h.timers.size,0);
});

async function startBounded(h) {
  await h.element('check-start').click();
  const ws=h.sockets[0]; ws.open(); ws.receive({type:'ready'}); await settle();
  ws.receive({type:'status',state:'thinking',generation:2});
  ws.receive({type:'sfu_track',generation:2}); await settle(); await h.run('measure(run)'); await settle();
  return ws;
}
test('bounded SFU mode requires captured decoded audio, a complete response, and closed resource diagnostics',async()=>{
  const diagnostics={};
  const h=harness({bounded:true,diagnostics});
  const zero=JSON.parse(h.run('JSON.stringify(Object.fromEntries(RESOURCE_KEYS.map(key=>[key,0])))'));
  Object.assign(zero,{closed:true,sfu_cleanup_unresolved:false,sfu_cleanup_persistence_failed:false,secret:'never copy'});
  Object.assign(diagnostics,zero);
  const ws=await startBounded(h);
  assert.equal(h.element('check-audio').volume,0);
  assert.equal(h.element('check-interrupt').disabled,true);
  assert.equal(h.sources.length,1); h.element('check-replay').click(); assert.equal(h.sources.length,1);
  h.sources[0].onended();
  ws.receive({type:'transcript',role:'user',text:'Hi.',final:true,generation:2});
  ws.receive({type:'transcript',role:'assistant',text:'Hello.',final:true});
  ws.receive({type:'status',state:'listening',generation:2});
  await h.run('measure(run)'); assert.equal([...h.timers.values()].some(t=>t.delay===1000),false);
  h.run('capture.port.onmessage({data:{pcm:new Int16Array([1,0,-1]).buffer}})');
  await h.run('measure(run)');
  [...h.timers.values()].find(t=>t.delay===1000).fn(); await settle();
  const result=JSON.parse(h.element('check-result').textContent);
  assert.equal(result.status,'completed'); assert.equal(result.cleanup.status,'released'); assert.equal(result.capture.nonzero_samples,2); assert.equal(result.capture.samples,3);
  assert.equal(result.supplied_source_revision,'a'.repeat(40));
  assert.equal(h.element('check-capture').hidden,false);
  assert.equal(ws.sent.at(-1).type,'end'); assert.equal(ws.sent.some(p=>p.type==='played'),false);
  const request=h.requests.find(r=>r.url.endsWith('/diagnostics'));
  assert.equal(request.options.headers['X-Session-Token'],'test-token'); assert.equal(request.url.includes('token'),false);
  assert.equal(h.run(`resourcesReleased(safeResources(${JSON.stringify(zero)}))`),true);
  assert.equal(h.run(`resourcesReleased(safeResources(${JSON.stringify({...zero,sfu_owned_adapters:1,sfu_cleanup_unresolved:true})}))`),false);
  assert.equal(h.element('check-result').textContent.includes('test-token'),false);
  assert.equal(h.run(`JSON.stringify(safeResources(${JSON.stringify(zero)}))`).includes('secret'),false);
});
test('bounded SFU mode rejects long input before opening a session and times out stalled calls',async()=>{
  const long=harness({bounded:true,duration:16}); await long.element('check-start').click();
  assert.equal(long.requests.length,0); assert.equal(JSON.parse(long.element('check-result').textContent).status,'failed');
  const h=harness({bounded:true}); await h.element('check-start').click();
  [...h.timers.values()].find(t=>t.delay===90000).fn(); await settle();
  assert.equal(JSON.parse(h.element('check-result').textContent).reason,'time_limit');
  assert.equal(h.contexts[0].state,'closed');
});
test('bounded completion cannot succeed after a newer generation supersedes its response',async()=>{
  const h=harness({bounded:true}), ws=await startBounded(h);
  h.sources[0].onended(); h.run('capture.port.onmessage({data:{pcm:new Int16Array([1]).buffer}})');
  ws.receive({type:'transcript',role:'user',text:'Hi.',final:true,generation:2});
  ws.receive({type:'transcript',role:'assistant',text:'Hello.',final:true});
  ws.receive({type:'status',state:'listening',generation:2}); await h.run('measure(run)');
  const finish=[...h.timers.values()].find(t=>t.delay===1000).fn;
  ws.receive({type:'clear',generation:3}); finish();
  assert.equal(JSON.parse(h.element('check-result').textContent).status,'running');
  h.element('check-end').click();
});

test('bounded capture stops at its memory limit and retains unresolved remote ownership',async()=>{
  const h=harness({bounded:true,diagnostics:{closed:true,sfu_owned_adapters:1,sfu_cleanup_unresolved:true}});
  await startBounded(h);
  h.run('captureBytes=960000; capture.port.onmessage({data:{pcm:new Int16Array([1]).buffer}})');
  await settle();
  assert.equal(JSON.parse(h.element('check-result').textContent).reason,'capture_limit');
  assert.equal(h.element('check-start').disabled,true);
  for(let i=0;i<7;i++) {
    const timer=[...h.timers.entries()].find(([,value])=>value.delay===500);
    assert.ok(timer); h.timers.delete(timer[0]); timer[1].fn(); await settle();
  }
  const result=JSON.parse(h.element('check-result').textContent);
  assert.equal(result.cleanup.status,'unresolved');
  assert.equal(result.cleanup.resources.sfu_owned_adapters,1);
  assert.equal(result.cleanup.attempts,8); assert.equal(h.element('check-start').disabled,false);
});

test('bounded SFU completion requires untagged assistant text from the current tagged response',async()=>{
  const h=harness({bounded:true}), ws=await startBounded(h);
  h.sources[0].onended(); h.run('capture.port.onmessage({data:{pcm:new Int16Array([1]).buffer}})');
  ws.receive({type:'transcript',role:'user',text:'Hi.',final:true});
  ws.receive({type:'transcript',role:'assistant',text:'Old answer.',final:true});
  ws.receive({type:'clear',generation:3});
  ws.receive({type:'status',state:'thinking',generation:3});
  ws.receive({type:'sfu_track',generation:3}); await settle(); await h.run('measure(run)'); await settle();
  ws.receive({type:'status',state:'listening',generation:3}); await h.run('measure(run)');
  assert.equal([...h.timers.values()].some(t=>t.delay===1000),false);
  // An older status cannot change the current response's ownership.
  ws.receive({type:'status',state:'thinking',generation:2});
  ws.receive({type:'transcript',role:'assistant',text:'Current answer.',final:true}); await h.run('measure(run)');
  const finish=[...h.timers.values()].find(t=>t.delay===1000);
  assert.ok(finish); finish.fn(); await settle();
  const result=JSON.parse(h.element('check-result').textContent);
  assert.equal(result.status,'completed'); assert.equal(result.generation,3);
  assert.equal(result.assistant_finals,2);
});

test('explicit stale or invalid transcript tags cannot complete a current SFU response',async()=>{
  const h=harness({bounded:true}), ws=await startBounded(h);
  h.sources[0].onended(); h.run('capture.port.onmessage({data:{pcm:new Int16Array([1]).buffer}})');
  ws.receive({type:'transcript',role:'user',text:'Hi.',final:true});
  ws.receive({type:'clear',generation:3}); ws.receive({type:'status',state:'thinking',generation:3});
  ws.receive({type:'sfu_track',generation:3}); await settle(); await h.run('measure(run)'); await settle();
  ws.receive({type:'status',state:'listening',generation:3});
  for(const generation of [2,null,'3']) {
    ws.receive({type:'transcript',role:'assistant',text:'Rejected answer.',final:true,generation});
    await h.run('measure(run)');
    assert.equal(h.run('assistantFinals'),0);
    assert.equal([...h.timers.values()].some(t=>t.delay===1000),false);
  }
  ws.receive({type:'transcript',role:'assistant',text:'Current answer.',final:true,generation:3});
  await h.run('measure(run)');
  const finish=[...h.timers.values()].find(t=>t.delay===1000); assert.ok(finish); finish.fn(); await settle();
  const result=JSON.parse(h.element('check-result').textContent);
  assert.equal(result.status,'completed'); assert.equal(result.generation,3); assert.equal(result.assistant_finals,1);
});

test('stale clear and error cannot detach the bounded SFU receiver or end its check',async()=>{
  const h=harness({bounded:true}), ws=await startBounded(h);
  const receiver=h.transports[0].output;
  ws.receive({type:'clear',generation:1});
  ws.receive({type:'error',generation:1,message:'Old error.'});
  assert.equal(h.transports[0].output,receiver);
  assert.equal(JSON.parse(h.element('check-result').textContent).status,'running');
  assert.equal(ws.sent.some(packet=>packet.type==='end'),false);
  h.element('check-end').click();
});
