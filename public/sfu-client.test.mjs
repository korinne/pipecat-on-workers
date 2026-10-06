// Signaling/lifecycle tests only. Fake peers cannot establish SFU interoperability
// or prove what a physical speaker played.
import test from 'node:test';
import assert from 'node:assert/strict';
import { getEventListeners } from 'node:events';
import { SfuAudioTransport } from './sfu-client.mjs';

const settle = () => new Promise(resolve => setImmediate(resolve));
const responseFor = body => body.action === 'publish'
  ? {sessionDescription:{type:'answer',sdp:'private-server-answer'}}
  : body.action === 'subscribe'
    ? {requiresImmediateRenegotiation:true,sessionDescription:{type:'offer',sdp:'private-server-offer'}}
    : {ok:true};
function setup({ signal, gather = true, connect = true, trackLate = false } = {}) {
  const peers = [], requests = [], states = [], errors = [], blocked = [];
  class Peer extends EventTarget {
    iceGatheringState = 'new'; connectionState = 'new'; signalingState = 'stable'; transceivers = []; closed = false;
    offers = 0; answers = 0; gathers = gather; operations = []; clientTransceivers = 0;
    constructor(config) { super(); this.config = config; peers.push(this); }
    addTransceiver(track, options) { this.clientTransceivers++; const t = { mid: null, track, ...options }; this.transceivers.push(t); return t; }
    async createOffer() { this.offers++; return {type:'offer',sdp:'private-offer'}; }
    async createAnswer() { assert.equal(this.signalingState,'have-remote-offer'); this.answers++; return {type:'answer',sdp:'private-answer'}; }
    async setLocalDescription(description) {
      assert.equal(this.closed,false);
      this.operations.push('local:'+description.type);
      if(description.type==='offer') { assert.equal(this.signalingState,'stable'); this.signalingState='have-local-offer'; }
      else { assert.equal(this.signalingState,'have-remote-offer'); this.signalingState='stable'; }
      this.localDescription = { ...description };
      this.iceGatheringState = 'gathering';
      this.transceivers.forEach((t,i)=>t.mid=String(i));
      if (this.gathers) this.finishGathering();
    }
    finishGathering() { this.iceGatheringState='complete'; this.localDescription.sdp += '-with-ice'; this.dispatchEvent(new Event('icegatheringstatechange')); }
    remoteTrack() {
      this.receivedTrack = {kind:'audio',stopped:false,stop(){this.stopped=true;}};
      const event=new Event('track'); event.track=this.receivedTrack; event.streams=[];
      this.ontrack?.(event); this.dispatchEvent(event);
    }
    async setRemoteDescription(description) {
      assert.equal(this.closed,false);
      this.operations.push('remote:'+description.type);
      if(description.type==='offer') {
        assert.equal(this.signalingState,'stable','server offer must not collide with a local offer');
        this.signalingState='have-remote-offer';
        this.transceivers.push({mid:'0',track:'audio',direction:'recvonly'});
      } else { assert.equal(this.signalingState,'have-local-offer'); this.signalingState='stable'; }
      this.remoteDescription=description;
      if (this.transceivers[0].direction==='recvonly' && !trackLate) this.remoteTrack();
      if(connect) this.connected();
    }
    connected() { this.connectionState='connected'; this.onconnectionstatechange?.(); this.dispatchEvent(new Event('connectionstatechange')); }
    close() { this.closed=true; this.connectionState='closed'; this.dispatchEvent(new Event('connectionstatechange')); }
  }
  class Stream { constructor(tracks) { this.tracks=tracks; } }
  const mic={kind:'audio',enabled:true}, stream={getAudioTracks:()=>[mic]};
  const audio={srcObject:null,muted:false,plays:0,pauses:0,async play(){this.plays++;},pause(){this.pauses++;}};
  const transport=new SfuAudioTransport({stream,audio,PeerConnection:Peer,MediaStreamClass:Stream,
    signal:async(body,abortSignal)=>{requests.push({body,abortSignal}); return signal ? signal(body,abortSignal) : responseFor(body);},
    onState:(role,state)=>states.push({role,state}),onError:e=>errors.push(e),onPlaybackBlocked:value=>blocked.push(value)});
  return {transport,peers,requests,states,errors,blocked,audio,mic};
}

test('microphone publish and response subscribe use separate peers with capability-free signaling bodies', async()=>{
  const h=setup();
  await h.transport.publish(); await h.transport.subscribe(4);
  assert.equal(h.peers.length,2);
  assert.equal(h.peers[0].transceivers[0].track,h.mic);
  assert.equal(h.peers[0].transceivers[0].direction,'sendonly');
  assert.equal(h.peers[1].transceivers[0].direction,'recvonly');
  assert.deepEqual(h.requests.map(r=>r.body.action),['publish','input_ready','subscribe','renegotiate','playback_ready']);
  assert.equal(h.requests[0].body.mid,'0'); assert.deepEqual(h.requests[2].body,{action:'subscribe',generation:4});
  assert.equal(h.peers[1].clientTransceivers,0); assert.equal(h.peers[1].offers,0); assert.equal(h.peers[1].answers,1);
  assert.deepEqual(h.peers[1].operations,['remote:offer','local:answer']);
  assert.match(h.requests[3].body.sessionDescription.sdp,/-with-ice$/);
  assert.ok(h.audio.srcObject); assert.equal(h.audio.muted,false); assert.equal(h.audio.plays,1);
  assert.ok(h.requests.every(r=>!('token' in r.body)&&r.body.action!=='played'));
  h.transport.close(); assert.ok(h.peers.every(p=>p.closed)); assert.equal(h.audio.srcObject,null);
});
test('interrupt detaches a generation immediately and stale tracks cannot reattach', async()=>{
  const h=setup(); await h.transport.publish(); await h.transport.subscribe(7);
  const old=h.peers[1], oldHandler=old.ontrack;
  h.transport.clearOutput(8);
  assert.equal(h.audio.muted,true); assert.equal(h.audio.srcObject,null); assert.equal(old.closed,true); assert.equal(old.receivedTrack.stopped,true);
  oldHandler({track:{kind:'audio'},streams:[{old:true}]}); assert.equal(h.audio.srcObject,null);
  await h.transport.subscribe(7); assert.equal(h.peers.length,2);
  await h.transport.subscribe(8); assert.equal(h.peers.length,3); assert.equal(h.peers[0].closed,false);
  assert.equal(h.requests.filter(r=>r.body.action==='playback_ready').length,2);
  h.transport.close();
});
test('End cancels gathering waits and late local descriptions cannot publish',async()=>{
  const h=setup({gather:false}); const pending=h.transport.publish();
  await settle(); assert.equal(h.transport.cleanups.size,1);
  h.transport.close(); await assert.rejects(pending,{name:'AbortError'});
  assert.equal(h.requests.length,0); assert.equal(h.transport.cleanups.size,0);
});
test('late signaling from a replaced receiver cannot attach or send readiness',async()=>{
  let release;
  const h=setup({signal:body=>body.action==='subscribe'&&body.generation===1?new Promise(resolve=>{release=resolve;}):responseFor(body)});
  await h.transport.publish(); const old=h.transport.subscribe(1); await settle();
  h.transport.clearOutput(2);
  assert.equal(h.requests.find(r=>r.body.action==='subscribe').abortSignal.aborted,true);
  await h.transport.subscribe(2); const currentSource=h.audio.srcObject;
  release({sessionDescription:{type:'offer',sdp:'stale'}}); await assert.rejects(old,{name:'AbortError'});
  assert.equal(h.audio.srcObject,currentSource);
  assert.deepEqual(h.requests.filter(r=>r.body.action==='playback_ready').map(r=>r.body.generation),[2]);
  h.transport.close();
});
test('clearing an output does not cancel an input connection still establishing',async()=>{
  const h=setup({connect:false}); const publishing=h.transport.publish(); await settle();
  h.transport.clearOutput(3); assert.equal(h.transport.cleanups.size,1);
  h.peers[0].connected(); await publishing; assert.equal(h.peers[0].closed,false);
  h.transport.close();
});
test('track arriving after connected still triggers readiness',async()=>{
  const h=setup({trackLate:true}); await h.transport.publish(); const subscribing=h.transport.subscribe(1); await settle();
  assert.equal(h.requests.some(r=>r.body.action==='playback_ready'),false);
  h.peers[1].remoteTrack(); await subscribing;
  assert.equal(h.requests.at(-1).body.action,'playback_ready'); h.transport.close();
});
test('blocked playback exposes an explicit resume without faking played receipts',async()=>{
  const h=setup(); h.audio.play=async()=>{throw new DOMException('blocked','NotAllowedError');};
  await h.transport.publish(); await h.transport.subscribe(1);
  assert.equal(h.blocked.at(-1),true);
  // Readiness is not blocked on audible playback, which would deadlock a sender
  // waiting for readiness before putting any media into the track.
  assert.equal(h.requests.at(-1).body.action,'playback_ready');
  h.audio.play=async()=>{}; await h.transport.resumePlayback(); assert.equal(h.blocked.at(-1),false);
  assert.equal(h.requests.some(r=>r.body.action==='played'),false); h.transport.close();
});
test('server output offers are answered through the owned generation',async()=>{
  const h=setup(); await h.transport.publish(); await h.transport.subscribe(9);
  const renegotiations=h.requests.filter(r=>r.body.action==='renegotiate').map(r=>r.body);
  assert.deepEqual(renegotiations.map(r=>r.role),['output']);
  assert.equal(renegotiations[0].generation,9);
  assert.equal(renegotiations[0].sessionDescription.type,'answer'); h.transport.close();
});
test('input_ready waits for the microphone connection and finishes before publish resolves',async()=>{
  let release;
  const h=setup({connect:false,signal:body=>body.action==='input_ready'?new Promise(resolve=>{release=resolve;}):responseFor(body)});
  let published=false; const publishing=h.transport.publish().then(()=>{published=true;}); await settle();
  assert.deepEqual(h.requests.map(r=>r.body.action),['publish']);
  h.peers[0].connected(); await settle();
  assert.deepEqual(h.requests.map(r=>r.body.action),['publish','input_ready']);
  assert.equal(published,false);
  release({ok:true}); await publishing; assert.equal(published,true); h.transport.close();
});
test('output answer completes ICE before serialized renegotiation and readiness',async()=>{
  let offer, negotiated;
  const h=setup({signal:body=>body.action==='subscribe'?new Promise(resolve=>{offer=resolve;}):body.action==='renegotiate'?new Promise(resolve=>{negotiated=resolve;}):responseFor(body)});
  await h.transport.publish(); const subscribing=h.transport.subscribe(11); await settle();
  const receiver=h.peers[1]; receiver.gathers=false;
  offer(responseFor({action:'subscribe'})); await settle();
  assert.equal(receiver.signalingState,'stable');
  assert.equal(h.requests.some(r=>r.body.action==='renegotiate'),false);
  receiver.finishGathering(); await settle();
  assert.equal(h.requests.at(-1).body.action,'renegotiate');
  assert.equal(h.requests.some(r=>r.body.action==='playback_ready'),false);
  negotiated({ok:true}); await subscribing;
  assert.equal(h.requests.at(-1).body.action,'playback_ready'); h.transport.close();
});
test('duplicate generation announcements cannot start concurrent negotiation',async()=>{
  let release;
  const h=setup({signal:body=>body.action==='subscribe'?new Promise(resolve=>{release=resolve;}):responseFor(body)});
  await h.transport.publish(); const pending=h.transport.subscribe(15); await settle();
  await h.transport.subscribe(15);
  assert.equal(h.peers.length,2); assert.equal(h.requests.filter(r=>r.body.action==='subscribe').length,1);
  release(responseFor({action:'subscribe'})); await pending;
  assert.equal(h.requests.filter(r=>r.body.action==='renegotiate').length,1);
  assert.equal(h.requests.filter(r=>r.body.action==='playback_ready').length,1); h.transport.close();
});
test('a generation canceled during renegotiation never sends playback readiness',async()=>{
  let release;
  const h=setup({signal:body=>body.action==='renegotiate'&&body.generation===3?new Promise(resolve=>{release=resolve;}):responseFor(body)});
  await h.transport.publish(); const previous=h.transport.subscribe(3); await settle();
  await h.transport.subscribe(4);
  const currentSource=h.audio.srcObject;
  release({ok:true}); await assert.rejects(previous,{name:'AbortError'});
  assert.equal(h.audio.srcObject,currentSource);
  assert.deepEqual(h.requests.filter(r=>r.body.action==='playback_ready').map(r=>r.body.generation),[4]); h.transport.close();
});
test('playback readiness does not await playing events or the audio play promise',async()=>{
  const h=setup(); h.audio.play=()=>new Promise(()=>{});
  await h.transport.publish(); await h.transport.subscribe(1);
  assert.equal(h.requests.at(-1).body.action,'playback_ready');
  assert.equal(h.requests.some(r=>r.body.action==='played'),false); h.transport.close();
});


const candidateSDP = 'a=candidate:1 1 udp 2122260223 192.0.2.1 40000 typ host';
function addPendingCandidate(peer) {
  peer.localDescription.sdp += `\r\n${candidateSDP}\r\n`;
  peer.dispatchEvent(new Event('icecandidate'));
}
function assertGatheringWaitRemoved(transport, peer) {
  assert.equal(getEventListeners(peer, 'icegatheringstatechange').length, 0);
  assert.equal([...transport.cleanups].filter(p=>p.role === (transport.input?.pc===peer?'input':'output')).length, 0);
}

test('gathering deadline publishes current candidates but input readiness still waits for connection', async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const h=setup({gather:false,connect:false}); const publishing=h.transport.publish();
  await settle(); const peer=h.peers[0];
  addPendingCandidate(peer);
  t.mock.timers.tick(7999); await settle(); assert.equal(h.requests.length,0);
  t.mock.timers.tick(1); await settle();
  assert.equal(peer.iceGatheringState,'gathering');
  assert.deepEqual(h.requests.map(r=>r.body.action),['publish']);
  assert.match(h.requests[0].body.sessionDescription.sdp,/a=candidate:/);
  assert.equal(getEventListeners(peer,'icegatheringstatechange').length,0);
  assert.equal(h.transport.cleanups.size,1,'connection wait still owns readiness');
  peer.connected(); await publishing;
  assert.deepEqual(h.requests.map(r=>r.body.action),['publish','input_ready']);
  assertGatheringWaitRemoved(h.transport,peer); h.transport.close();
  t.mock.timers.tick(20000); await settle();
  assert.equal(h.requests.length,2,'late timer does not repeat publication');
});
test('gathering deadline renegotiates current answer candidates but output readiness waits for connection', async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const h=setup({gather:false,connect:false}); const subscribing=h.transport.subscribe(31);
  await settle(); const peer=h.peers[0]; addPendingCandidate(peer);
  assert.deepEqual(h.requests.map(r=>r.body.action),['subscribe']);
  t.mock.timers.tick(8000); await settle();
  assert.equal(peer.iceGatheringState,'gathering');
  assert.deepEqual(h.requests.map(r=>r.body.action),['subscribe','renegotiate']);
  assert.match(h.requests[1].body.sessionDescription.sdp,/a=candidate:/);
  assert.equal(h.requests[1].body.sessionDescription.type,'answer');
  assert.equal(h.requests[1].body.generation,31);
  assert.equal(getEventListeners(peer,'icegatheringstatechange').length,0);
  peer.connected(); await subscribing;
  assert.equal(h.requests.at(-1).body.action,'playback_ready');
  assertGatheringWaitRemoved(h.transport,peer); h.transport.close();
});
for (const role of ['input','output']) {
  test(`${role} gathering deadline still rejects without a candidate`,async t=>{
    t.mock.timers.enable({apis:['setTimeout']});
    const h=setup({gather:false});
    const pending=role==='input'?h.transport.publish():h.transport.subscribe(32);
    await settle(); const peer=h.peers[0];
    // Mentioning a candidate elsewhere in SDP is not an ICE candidate line.
    peer.localDescription.sdp+='\r\na=label:not-a=candidate:route\r\n';
    t.mock.timers.tick(8000);
    await assert.rejects(pending,/could not gather a network route/);
    assert.deepEqual(h.requests.map(r=>r.body.action),role==='input'?[]:['subscribe']);
    assertGatheringWaitRemoved(h.transport,peer); h.transport.close();
  });
  test(`End cancels ${role} gathering even when a candidate is available at the deadline`,async t=>{
    t.mock.timers.enable({apis:['setTimeout']});
    const h=setup({gather:false});
    const pending=role==='input'?h.transport.publish():h.transport.subscribe(33);
    await settle(); const peer=h.peers[0]; addPendingCandidate(peer);
    t.mock.timers.tick(7999);
    h.transport.close(); await assert.rejects(pending,{name:'AbortError'});
    assertGatheringWaitRemoved(h.transport,peer);
    t.mock.timers.tick(1); peer.finishGathering(); await settle();
    assert.deepEqual(h.requests.map(r=>r.body.action),role==='input'?[]:['subscribe']);
  });
}
test('replacement during candidate gathering cannot renegotiate or announce readiness for the old output',async t=>{
  t.mock.timers.enable({apis:['setTimeout']});
  const h=setup({gather:false}); const old=h.transport.subscribe(40);
  await settle(); const retired=h.peers[0]; addPendingCandidate(retired);
  // The deadline resolves the old wait; replacement wins before its awaiting
  // continuation can submit the answer or report readiness.
  t.mock.timers.tick(8000);
  const replacement=h.transport.subscribe(41);
  await assert.rejects(old,{name:'AbortError'}); await settle();
  const current=h.peers[1]; current.finishGathering(); await replacement;
  const source=h.audio.srcObject;
  t.mock.timers.tick(8000); retired.finishGathering(); await settle();
  assert.equal(h.audio.srcObject,source);
  assert.deepEqual(h.requests.filter(r=>r.body.action==='renegotiate').map(r=>r.body.generation),[41]);
  assert.deepEqual(h.requests.filter(r=>r.body.action==='playback_ready').map(r=>r.body.generation),[41]);
  assert.equal(h.transport.cleanups.size,0);
  assert.equal(getEventListeners(retired,'icegatheringstatechange').length,0);
  assert.equal(getEventListeners(current,'icegatheringstatechange').length,0);
  h.transport.close();
});

for (const role of ['input','output']) {
  test(`${role} candidates do not bypass the connection timeout`,async t=>{
    t.mock.timers.enable({apis:['setTimeout']});
    const h=setup({gather:false,connect:false});
    const pending=role==='input'?h.transport.publish():h.transport.subscribe(50);
    await settle(); const peer=h.peers[0]; addPendingCandidate(peer);
    t.mock.timers.tick(8000); await settle();
    assert.equal(h.requests.some(r=>['input_ready','playback_ready'].includes(r.body.action)),false);
    t.mock.timers.tick(15000);
    await assert.rejects(pending,role==='input'?/microphone could not connect/:/audio could not connect/);
    assert.equal(h.requests.some(r=>['input_ready','playback_ready'].includes(r.body.action)),false);
    assertGatheringWaitRemoved(h.transport,peer); h.transport.close();
  });
}

for (const role of ['input','output']) {
  test(`End after ${role} gathering deadline wins before negotiation resumes`,async t=>{
    t.mock.timers.enable({apis:['setTimeout']});
    const h=setup({gather:false});
    const pending=role==='input'?h.transport.publish():h.transport.subscribe(60);
    await settle(); const peer=h.peers[0]; addPendingCandidate(peer);
    t.mock.timers.tick(8000);
    h.transport.close(); await assert.rejects(pending,{name:'AbortError'});
    assertGatheringWaitRemoved(h.transport,peer);
    peer.finishGathering(); t.mock.timers.tick(20000); await settle();
    assert.deepEqual(h.requests.map(r=>r.body.action),role==='input'?[]:['subscribe']);
  });
}
