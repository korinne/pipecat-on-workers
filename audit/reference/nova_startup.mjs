export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.hostname !== '127.0.0.1' || request.method !== 'POST') return new Response('Local POST only', { status: 403 });
    if (!['/original', '/strings'].includes(url.pathname)) return new Response('Unknown case', { status: 404 });
    const inputs = { encoding: 'linear16', sample_rate: '16000', channels: 1, language: 'en-US', interim_results: true, vad_events: true, endpointing: '200' };
    if (url.pathname === '/strings') Object.assign(inputs, { channels: '1', interim_results: 'true', vad_events: 'true' });
    const report = { variant: url.pathname.slice(1), model: '@cf/deepgram/nova-3', inputs, audio_sent_bytes: 0 };
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await env.AI.run(report.model, inputs, { websocket: true, signal: controller.signal });
      report.status = response.status;
      report.websocket_returned = !!response.webSocket;
      if (response.webSocket) {
        response.webSocket.accept();
        response.webSocket.close(1000, 'Startup contract probe complete');
      } else {
        const reader = response.body?.getReader();
        if (reader) {
          let text = '';
          try {
            while (text.length < 4096) { const part = await reader.read(); if (part.done) break; text += new TextDecoder().decode(part.value); }
            // Known provider error fields only; exclude IDs and response headers.
            const value = JSON.parse(text);
            const pick = x => typeof x === 'string' ? x.slice(0, 600) : x && typeof x === 'object' ? Object.fromEntries(['code', 'message', 'error', 'errors'].filter(k => k in x).map(k => [k, Array.isArray(x[k]) ? x[k].slice(0, 4).map(pick) : pick(x[k])])) : x;
            report.error = pick(value);
          } catch { report.error_body = 'Unparseable or unavailable'; }
          finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
        }
      }
    } catch (e) { report.exception = { name: e.name, message: e.message.slice(0, 600) }; }
    finally { clearTimeout(timer); controller.abort(); }
    return Response.json(report);
  }
};
