// Cloudflare SFU carries audio; the application WebSocket carries control.
// Every assistant generation gets a fresh receiving PeerConnection. Closing it
// detaches the old jitter buffer without pretending that sent audio was played.
export class SfuAudioTransport {
  constructor({ stream, audio, signal, onState = () => {}, onError = () => {}, onPlaybackBlocked = () => {}, PeerConnection = globalThis.RTCPeerConnection, MediaStreamClass = globalThis.MediaStream }) {
    if (!PeerConnection) throw new Error('This browser does not support WebRTC. Try the WebSocket example.');
    Object.assign(this, { stream, audio, signal, onState, onError, onPlaybackBlocked, PeerConnection, MediaStreamClass });
    this.closed = false;
    this.input = null;
    this.output = null;
    this.floor = -1;
    this.controllers = new Set();
    this.cleanups = new Set();
    this.config = { bundlePolicy: 'max-bundle', iceServers: [{ urls: 'stun:stun.cloudflare.com:3478' }] };
  }
  assertCurrent(peer, role) {
    if (this.closed || this[role]?.pc !== peer) throw new DOMException('Media connection was replaced.', 'AbortError');
  }
  async request(body, peer, role) {
    this.assertCurrent(peer, role);
    const controller = new AbortController();
    const pending = { controller, role };
    this.controllers.add(pending);
    const timeout = setTimeout(() => controller.abort(), 20000);
    try {
      const result = await this.signal(body, controller.signal);
      this.assertCurrent(peer, role);
      return result;
    } catch (error) {
      this.assertCurrent(peer, role);
      if (controller.signal.aborted) throw new Error('The SFU request timed out. Start a new conversation.');
      throw error;
    } finally { clearTimeout(timeout); this.controllers.delete(pending); }
  }
  async waitFor(peer, role, predicate, eventNames, timeoutMs, message, timeoutPredicate = () => false) {
    this.assertCurrent(peer, role);
    if (predicate()) return;
    const names = Array.isArray(eventNames) ? eventNames : [eventNames];
    await new Promise((resolve, reject) => {
      let timer;
      const finish = error => {
        clearTimeout(timer);
        for (const name of names) peer.removeEventListener(name, changed);
        this.cleanups.delete(pending);
        error ? reject(error) : resolve();
      };
      const changed = () => {
        try { this.assertCurrent(peer, role); if (predicate()) finish(); }
        catch (error) { finish(error); }
      };
      const cancel = () => finish(new DOMException('Media connection was replaced.', 'AbortError'));
      const pending = { cancel, role };
      this.cleanups.add(pending);
      for (const name of names) peer.addEventListener(name, changed);
      timer = setTimeout(() => {
        try { this.assertCurrent(peer, role); finish(timeoutPredicate() ? undefined : new Error(message)); }
        catch (error) { finish(error); }
      }, timeoutMs);
      changed();
    });
  }
  makePeer(role, generation = -1) {
    if (this.closed) throw new DOMException('Media connection was closed.', 'AbortError');
    if (this[role]) throw new Error('A media connection already exists for this role.');
    const pc = new this.PeerConnection(this.config);
    const record = { pc, generation, track: null, source: null };
    this[role] = record;
    pc.onconnectionstatechange = () => {
      if (this.closed || this[role] !== record) return;
      this.onState(role, pc.connectionState);
      if (pc.connectionState === 'failed') this.onError(new Error('The WebRTC audio connection failed. Start again to reconnect.'));
    };
    return record;
  }
  async localDescription(peer, role, type) {
    this.assertCurrent(peer, role);
    const description = await (type === 'offer' ? peer.createOffer() : peer.createAnswer());
    this.assertCurrent(peer, role);
    await peer.setLocalDescription(description);
    this.assertCurrent(peer, role);
    // Signaling does not trickle candidates. Bound gathering, then allow the
    // gathered candidates to attempt connectivity if other lookups are pending.
    // publish/subscribe still require a connected peer before reporting ready.
    const hasCandidate = () => peer.localDescription?.sdp.split(/\r?\n/).some(line => line.startsWith('a=candidate:')) === true;
    await this.waitFor(peer, role, () => peer.iceGatheringState === 'complete', 'icegatheringstatechange', 8000,
      'WebRTC could not gather a network route. Try another network or the WebSocket example.', hasCandidate);
    this.assertCurrent(peer, role);
    return { type: peer.localDescription.type, sdp: peer.localDescription.sdp };
  }
  async applyResponse(result, peer, role, generation) {
    this.assertCurrent(peer, role);
    const expectedType = role === 'output' ? 'offer' : 'answer';
    if (result?.sessionDescription?.type !== expectedType) throw new Error('The server returned an invalid WebRTC negotiation.');
    await peer.setRemoteDescription(result.sessionDescription);
    this.assertCurrent(peer, role);
    if (role === 'output') {
      const sessionDescription = await this.localDescription(peer, role, 'answer');
      await this.request({ action: 'renegotiate', role, generation, sessionDescription }, peer, role);
    } else if (result.requiresImmediateRenegotiation) {
      // Publishing returns an answer. Do not guess at an extra offer exchange.
      throw new Error('The SFU requested an unsupported negotiation. Start a new conversation.');
    }
  }
  async publish() {
    const { pc } = this.makePeer('input');
    const track = this.stream.getAudioTracks()[0];
    if (!track) throw new Error('No microphone track is available.');
    const sender = pc.addTransceiver(track, { direction: 'sendonly', streams: [this.stream] });
    const sessionDescription = await this.localDescription(pc, 'input', 'offer');
    const result = await this.request({ action: 'publish', sessionDescription, mid: sender.mid }, pc, 'input');
    await this.applyResponse(result, pc, 'input');
    await this.waitFor(pc, 'input', () => pc.connectionState === 'connected', 'connectionstatechange', 15000, 'The microphone could not connect to the SFU. Try the WebSocket example.');
    this.assertCurrent(pc, 'input');
    // Create the egress adapter only after the microphone peer is connected.
    await this.request({ action: 'input_ready' }, pc, 'input');
    this.onState('input', 'connected');
  }
  async subscribe(generation) {
    if (this.closed || !Number.isSafeInteger(generation) || generation < this.floor) return;
    if (this.output?.generation === generation) return;
    this.clearOutput(generation);
    const record = this.makePeer('output', generation), { pc } = record;
    // The SFU initiates output negotiation with its offer. Starting a local
    // offer here would leave the receiver in have-local-offer and cause glare.
    pc.ontrack = event => {
      if (this.closed || this.output !== record || generation < this.floor || event.track.kind !== 'audio') return;
      record.track = event.track;
      record.source = event.streams?.[0] || new this.MediaStreamClass([event.track]);
      this.audio.srcObject = record.source;
      this.audio.muted = false;
      this.resumePlayback().catch(() => {});
    };
    const result = await this.request({ action: 'subscribe', generation }, pc, 'output');
    await this.applyResponse(result, pc, 'output', generation);
    await this.waitFor(pc, 'output', () => Boolean(record.track) && pc.connectionState === 'connected', ['connectionstatechange', 'track'], 15000, 'The agent’s audio could not connect. Start a new conversation.');
    this.assertCurrent(pc, 'output');
    // Ready to receive is deliberately distinct from actually played.
    await this.request({ action: 'playback_ready', generation }, pc, 'output');
  }
  async resumePlayback() {
    const current = this.output;
    if (this.closed || !current?.track) return;
    try {
      await this.audio.play();
      if (!this.closed && this.output === current) this.onPlaybackBlocked(false);
    } catch (error) {
      if (!this.closed && this.output === current) this.onPlaybackBlocked(true);
      throw error;
    }
  }
  clearOutput(generation) {
    const began = performance.now();
    this.floor = Math.max(this.floor, generation ?? (this.output?.generation ?? this.floor) + 1);
    this.audio.muted = true;
    this.audio.pause();
    this.audio.srcObject = null;
    if (this.output) {
      this.output.pc.ontrack = this.output.pc.onconnectionstatechange = null;
      this.output.pc.close();
      this.output.track?.stop();
    }
    this.output = null;
    for (const pending of [...this.controllers]) if (pending.role === 'output') pending.controller.abort();
    for (const pending of [...this.cleanups]) if (pending.role === 'output') pending.cancel();
    this.onPlaybackBlocked(false);
    return performance.now() - began;
  }
  close() {
    if (this.closed) return;
    this.closed = true;
    this.clearOutput();
    if (this.input) { this.input.pc.onconnectionstatechange = null; this.input.pc.close(); this.input = null; }
    for (const pending of this.controllers) pending.controller.abort();
    for (const pending of [...this.cleanups]) pending.cancel();
  }
}
