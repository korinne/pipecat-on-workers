import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function harness({publish,fetchSession}={}) {
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
    async decodeAudioData(){return {duration:3};}
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
    window:{AudioContext:Context,RTCPeerConnection:class {},addEventListener(){}},
    WebSocket:Socket,performance,URL,AbortSignal,Uint8Array,Float32Array,location:{href:'https://voice.example/transport-check',protocol:'https:'},
    fetch:async(url,options)=>{requests.push({url,options});if(url==='/api/session'&&fetchSession)return fetchSession();return {ok:true,json:async()=>url==='/api/session'?{id:'test-id',token:'test-token'}:{ok:true}};},
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
