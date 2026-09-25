"""Adapter protocol/cancellation checks using explicitly fake JS/provider I/O."""
import pathlib
import asyncio
import importlib.util
import json
import sys
import types
from types import SimpleNamespace as N

class Uint:
    @staticmethod
    def new(value):
        return N(to_py=lambda: memoryview(value))
class Proxy:
    def __init__(self, callback): self.callback=callback; self.destroyed=False
    def destroy(self): self.destroyed=True
js = types.ModuleType('js')
js.Object=N(fromEntries=lambda value:value)
js.Uint8Array=Uint
sys.modules['js']=js
ffi = types.ModuleType('pyodide.ffi')
ffi.create_proxy=Proxy
ffi.to_js=lambda value, **kwargs:value
sys.modules['pyodide']=types.ModuleType('pyodide')
sys.modules['pyodide.ffi']=ffi
spec=importlib.util.spec_from_file_location('providers',pathlib.Path(__file__).resolve().parents[1] / 'src/providers.py')
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

class Reader:
    # Match raw Pyodide JsProxy readers, which cannot be members of a set.
    __hash__ = None
    def __init__(self, chunks): self.chunks=list(chunks); self.canceled=False; self.released=False
    async def read(self):
        await asyncio.sleep(0)
        return N(done=False,value=self.chunks.pop(0)) if self.chunks else N(done=True)
    async def cancel(self,*args): self.canceled=True
    def releaseLock(self): self.released=True
class AI:
    def __init__(self,result): self.result=result; self.calls=[]
    async def run(self,*args): self.calls.append(args); return self.result
class JsNull:
    def __bool__(self): return False
class WS:
    def __init__(self): self.readyState=1; self.callbacks={}; self.sent=[]; self.closed=False
    def accept(self): pass
    def addEventListener(self,event,proxy): self.callbacks[event]=proxy
    def removeEventListener(self,event,proxy): self.callbacks.pop(event,None)
    def close(self,*args): self.closed=True; self.readyState=3
    def send(self,value): self.sent.append(value)

async def main():
    encoded=('data: '+json.dumps({'response':'Hello 🐈'},ensure_ascii=False)+'\r\n\r\n'+'data: {"response":" world"}\n\ndata: [DONE]\n\n').encode()
    reader=Reader([encoded[i:i+1] for i in range(len(encoded))])
    p=m.WorkersProviders(N(AI=AI(N(getReader=lambda:reader))),lambda event:None)
    assert ''.join([token async for token in p.generate([])])=='Hello 🐈 world'
    assert reader.canceled and reader.released
    assert p.diagnostics()['provider_readers']==0
    # Real Workers AI SSE uses numeric `response` values for numeric tokens;
    # choices.delta.content preserves the text, including formatting and zero.
    events=[
        {'response':1.5,'choices':[{'delta':{'content':'1.5'}}]},
        {'response':0,'choices':[{'delta':{'content':'0'}}]},
        {'response':1.5,'choices':[{'delta':{'content':'1.50'}}]},
        {'response':'must not leak','choices':[{'delta':{'content':''}}]},
        {'choices':[],'usage':{'completion_tokens':0}},
        {'choices':[{'delta':{},'finish_reason':'stop'}]},
        {'response':'','usage':{'completion_tokens':3}},
        {'response':False},
        {'response':1.5},
        {'response':' fallback'},
    ]
    reader=Reader([('data: '+json.dumps(event)+'\n\n').encode() for event in events]+[b'data: [DONE]\n\n'])
    p.env.AI=AI(N(getReader=lambda:reader))
    assert [token async for token in p.generate([])]==['1.5','0','1.50',' fallback']
    assert reader.canceled and reader.released and p.diagnostics()['provider_readers']==0
    reader=Reader([b'data: {"response":"first"}\n\n',b'data: {"response":"second"}\n\n'])
    p.env.AI=AI(N(getReader=lambda:reader))
    generator=p.generate([])
    assert await generator.__anext__()=='first'
    await generator.aclose()
    assert reader.canceled and reader.released
    assert p.diagnostics()['provider_readers']==0

    # Each active reader must remain independently owned until its generator
    # exits, including when provider shutdown cancels a reader paused at yield.
    first=Reader([b'data: {"response":"first"}\n\n'])
    second=Reader([b'data: {"response":"second"}\n\n'])
    owners=m.WorkersProviders(N(AI=AI(N(getReader=lambda:first))),lambda event:None)
    first_generation=owners.generate([])
    assert await first_generation.__anext__()=='first'
    owners.env.AI=AI(N(getReader=lambda:second))
    second_generation=owners.generate([])
    assert await second_generation.__anext__()=='second'
    assert owners.diagnostics()['provider_readers']==2
    await first_generation.aclose()
    assert first.canceled and first.released and not second.canceled
    assert owners.diagnostics()['provider_readers']==1
    await owners.close()
    assert second.canceled
    await second_generation.aclose()
    assert second.released and owners.diagnostics()['provider_readers']==0

    class SlowCancelReader(Reader):
        def __init__(self):
            super().__init__([b'data: {"response":"pending cleanup"}\n\n'])
            self.cancel_started=asyncio.Event()
        async def cancel(self,*args):
            self.cancel_started.set()
            await asyncio.Event().wait()
    slow=SlowCancelReader()
    cleanup=m.WorkersProviders(N(AI=AI(N(getReader=lambda:slow))),lambda event:None)
    generation=cleanup.generate([])
    assert await generation.__anext__()=='pending cleanup'
    closing=asyncio.create_task(generation.aclose())
    await asyncio.wait_for(slow.cancel_started.wait(),1)
    assert cleanup.diagnostics()['provider_readers']==1
    closing.cancel()
    try: await closing
    except asyncio.CancelledError: pass
    else: raise AssertionError('reader cleanup swallowed cancellation')
    assert slow.released and cleanup.diagnostics()['provider_readers']==0
    await cleanup.close()

    stt_ws=WS()
    stt=m.WorkersProviders(N(AI=AI(N(webSocket=stt_ws))),lambda event:None)
    await stt.start()
    model,parameters,options=stt.env.AI.calls[-1]
    assert model==m.STT_MODEL and parameters['eot_threshold']=='0.9'
    assert parameters['eot_timeout_ms']=='5000' and parameters['sample_rate']=='16000'
    assert options=={'websocket':True}
    await stt.close()
    assert stt_ws.closed and not stt_ws.callbacks

    # A failed upgrade has a falsey JS null proxy rather than Python None.
    p.env.AI=AI(N(webSocket=JsNull(),status=502))
    try: await p._socket('tts',{},'tts')
    except m.ProviderConnectionError as exc:
        assert exc.status==502 and exc.code is None and not exc.recoverable
        assert str(exc)=='Speech synthesis connection could not be established (HTTP 502). Please try again.'
    else: raise AssertionError('JS null WebSocket did not fail explicitly')
    assert p.diagnostics()['provider_sockets']==0
    late_body=Reader([])
    resolved=asyncio.get_running_loop().create_future()
    resolved.set_result(N(webSocket=JsNull(),body=late_body))
    p._discard_late_result(resolved)
    await asyncio.sleep(0.01)
    assert late_body.canceled

    ws=WS(); socket=m._Socket(ws,'test',max_bytes=4)
    socket._message(N(data=b'123'))
    assert await socket.receive()==b'123'
    socket._message(N(data=b'12345'))
    try: await socket.receive()
    except m.ProviderError: pass
    else: raise AssertionError('overflow did not fail')
    assert ws.closed and len(ws.callbacks)==0
    ws=WS(); p.env.AI=AI(N(webSocket=ws))
    generator=p.synthesize('hello')
    task=asyncio.create_task(generator.__anext__())
    await asyncio.sleep(0.01)
    model,parameters,options=p.env.AI.calls[-1]
    assert model==m.TTS_MODEL and parameters['sample_rate']=='24000'
    assert options=={'websocket':True}
    assert any(json.loads(x).get('type')=='Speak' for x in ws.sent)
    ws.callbacks['message'].callback(N(data=b'\x00\x00\x01\x01'))
    assert await task==b'\x00\x00\x01\x01'
    await generator.aclose()
    assert ws.closed and not ws.callbacks
    assert p.diagnostics()['provider_sockets']==0
    gate=asyncio.Event()
    late_ws=WS()
    class DelayedAI:
        async def run(self,*args):
            await gate.wait()
            return N(webSocket=late_ws)
    p.env.AI=DelayedAI()
    opening=asyncio.create_task(p._socket('tts',{},'tts'))
    await asyncio.sleep(0)
    opening.cancel()
    try: await opening
    except asyncio.CancelledError: pass
    gate.set()
    await asyncio.sleep(0.01)
    assert late_ws.closed
    assert p.diagnostics()['pending_provider_requests']==0
    await p.close()
    assert all(p.diagnostics()[name]==0 for name in ['provider_tasks','provider_sockets','provider_readers','queued_provider_bytes'])
    await connection_failures()
    await connection_retries()
    await cleanup_ownership()
    print('PASS: provider protocol, unhashable reader ownership, bounded sanitized upgrade errors, quota/capacity classification, bounded capacity retries, cancellation/deadline handling, partial socket setup cleanup, late cleanup ownership, SSE, PCM and explicit shutdown (fake I/O only)')


def rejected(payload, *, retry_after=None, status=429):
    encoded=payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    reader=Reader([encoded[:5],encoded[5:]])
    return N(webSocket=JsNull(),status=status,body=N(getReader=lambda:reader),
             headers=N(get=lambda name:retry_after)), reader


async def connection_failures():
    for payload,code,word in [
        ({'internalCode':3040,'description':'PRIVATE UPSTREAM TEXT'},3040,'capacity'),
        ({'errors':[{'code':3036,'message':'PRIVATE UPSTREAM TEXT'}]},3036,'quota'),
        ({'error':{'code':3040}},3040,'capacity'),
        ({'internalCode':True,'description':'PRIVATE UPSTREAM TEXT'},None,'HTTP 429'),
        ({'internalCode':3040.0},3040,'capacity'),
        ({'internalCode':3040.5},None,'HTTP 429'),
        ({'internalCode':9999},None,'HTTP 429'),
        (b'not JSON PRIVATE UPSTREAM TEXT',None,'HTTP 429'),
        (b'\xff',None,'HTTP 429'),
        ([],None,'HTTP 429'),
        (b'x'*(m.MAX_ERROR_BODY_BYTES+1),None,'HTTP 429'),
    ]:
        response,reader=rejected(payload,retry_after='1.5')
        provider=m.WorkersProviders(N(AI=AI(response)),lambda event:None)
        try: await provider._socket('stt',{},'stt')
        except m.ProviderConnectionError as exc:
            assert exc.status==429 and exc.code==code and exc.retry_after==1.5
            assert not exc.recoverable and word in str(exc)
            assert 'PRIVATE' not in str(exc) and 'description' not in str(exc)
        else: raise AssertionError('Rejected connection did not fail')
        assert reader.canceled and reader.released and provider.diagnostics()['provider_readers']==0
        await provider.close()
    for value in ('-1','NaN','Infinity','Thu, 24 Sep 2026 23:00:00 GMT','garbage',None):
        response,_=rejected({},retry_after=value)
        assert m._retry_after(response) is None
    response,_=rejected({},retry_after='301')
    assert m._retry_after(response)==m.MAX_RETRY_AFTER
    assert m.ProviderConnectionError('stt',status=429.0).status==429
    for status in (True,'429',429.5,float('nan'),float('inf')):
        assert m.ProviderConnectionError('stt',status=status).status is None

    class HangingReader(Reader):
        def __init__(self): super().__init__([]); self.started=asyncio.Event()
        async def read(self): self.started.set(); await asyncio.Event().wait()
    reader=HangingReader()
    provider=m.WorkersProviders(N(AI=AI(N(webSocket=JsNull(),status=429,
                                      body=N(getReader=lambda:reader)))),lambda event:None)
    opening=asyncio.create_task(provider._socket('stt',{},'stt'))
    await reader.started.wait()
    assert provider.diagnostics()['provider_readers']==1
    opening.cancel()
    try: await opening
    except asyncio.CancelledError: pass
    else: raise AssertionError('Error-body parsing swallowed cancellation')
    assert reader.canceled and reader.released and provider.diagnostics()['provider_readers']==0
    await provider.close()

    # A body that never completes cannot prolong startup beyond its read budget.
    reader=HangingReader()
    provider=m.WorkersProviders(N(AI=AI(N(webSocket=JsNull(),status=429,
                                      body=N(getReader=lambda:reader)))),lambda event:None)
    original=m.ERROR_BODY_TIMEOUT; m.ERROR_BODY_TIMEOUT=0.02
    try:
        started=asyncio.get_running_loop().time()
        try: await provider._socket('stt',{},'stt')
        except m.ProviderConnectionError as exc: assert exc.code is None and not exc.recoverable
        else: raise AssertionError('Unending response body did not time out')
        assert asyncio.get_running_loop().time()-started<0.5
        assert reader.canceled and reader.released
    finally: m.ERROR_BODY_TIMEOUT=original
    await provider.close()


class SequenceAI:
    def __init__(self,results): self.results=list(results); self.calls=0
    async def run(self,*args):
        self.calls+=1
        return self.results.pop(0)


async def connection_retries():
    response,reader=rejected({'internalCode':3040})
    ws=WS(); ai=SequenceAI([response,N(webSocket=ws)])
    events=[]; provider=m.WorkersProviders(N(AI=ai),events.append)
    await provider.start()
    assert ai.calls==2 and reader.canceled and reader.released
    assert [event['delay_ms'] for event in events if event.get('status')=='retrying']==[1000]
    assert provider.connection_generation==1
    await provider.close()
    assert ws.closed

    errors=[rejected({'internalCode':3040}) for _ in range(3)]
    ai=SequenceAI([response for response,_ in errors]); events=[]
    provider=m.WorkersProviders(N(AI=ai),events.append)
    started=asyncio.get_running_loop().time()
    try: await provider.start()
    except m.ProviderConnectionError as exc: assert exc.code==3040 and not exc.recoverable
    else: raise AssertionError('Capacity retries did not exhaust')
    assert ai.calls==3 and asyncio.get_running_loop().time()-started<18
    assert [event['delay_ms'] for event in events if event.get('status')=='retrying']==[1000,2000]
    assert all(reader.canceled and reader.released for _,reader in errors)
    await provider.close()

    for payload,wait in [({'internalCode':3036},None),({},None),({'internalCode':3040},'60'),({'internalCode':3040},'301')]:
        response,reader=rejected(payload,retry_after=wait)
        ai=SequenceAI([response]); provider=m.WorkersProviders(N(AI=ai),lambda event:None)
        try: await provider.start()
        except m.ProviderConnectionError: pass
        else: raise AssertionError('Nonretryable/over-budget response did not stop')
        assert ai.calls==1 and reader.canceled and reader.released
        await provider.close()

    response,reader=rejected({'internalCode':3040},retry_after='0')
    ai=SequenceAI([response,N(webSocket=WS())]); events=[]
    provider=m.WorkersProviders(N(AI=ai),events.append)
    await provider.start()
    assert ai.calls==2 and events[0]['delay_ms']==0
    await provider.close()

    retrying=asyncio.Event(); response,_=rejected({'internalCode':3040})
    ai=SequenceAI([response]); provider=m.WorkersProviders(N(AI=ai),lambda event:retrying.set())
    opening=asyncio.create_task(provider.start()); await retrying.wait(); opening.cancel()
    try: await opening
    except asyncio.CancelledError: pass
    else: raise AssertionError('Retry sleep swallowed cancellation')
    assert ai.calls==1
    await provider.close()

    response,_=rejected({'internalCode':3040}); ai=SequenceAI([response])
    async def close_on_retry(event): await provider.close()
    provider=m.WorkersProviders(N(AI=ai),close_on_retry)
    try: await provider.start()
    except m.ProviderError: pass
    else: raise AssertionError('Closed provider retried startup')
    assert ai.calls==1

    # Timeout never creates another request while its shielded JS promise lives.
    gate=asyncio.Event(); late_ws=WS()
    class SlowAI:
        calls=0
        async def run(self,*args):
            self.calls+=1; await gate.wait(); return N(webSocket=late_ws)
    ai=SlowAI(); provider=m.WorkersProviders(N(AI=ai),lambda event:None)
    original=m.STT_CONNECT_BUDGET
    m.STT_CONNECT_BUDGET=m.ERROR_BODY_CLEANUP_TIMEOUT+0.03
    try:
        try: await provider.start()
        except m.ProviderConnectionError as exc:
            assert exc.code is None and not exc.recoverable and 'timed out' in str(exc)
        else: raise AssertionError('Slow startup did not time out')
        assert ai.calls==1 and provider.diagnostics()['pending_provider_requests']==1
        await provider.close()
        gate.set(); await asyncio.sleep(0.01)
        assert late_ws.closed and provider.diagnostics()['pending_provider_requests']==0
    finally: m.STT_CONNECT_BUDGET=original


async def cleanup_ownership():
    class BrokenAccept(WS):
        def __init__(self): super().__init__(); self.proxies=[]
        def addEventListener(self,event,proxy): self.proxies.append(proxy); super().addEventListener(event,proxy)
        def accept(self): raise RuntimeError('simulated accept failure')
        def removeEventListener(self,event,proxy):
            super().removeEventListener(event,proxy)
            raise RuntimeError('simulated removal failure')
    ws=BrokenAccept()
    try: m._Socket(ws,'stt')
    except RuntimeError: pass
    else: raise AssertionError('Broken accept did not fail')
    assert ws.closed and not ws.callbacks and all(proxy.destroyed for proxy in ws.proxies)

    class SlowBody:
        def __init__(self): self.started=False; self.gate=asyncio.Event()
        async def cancel(self,*args): self.started=True; await self.gate.wait()
    body=SlowBody(); provider=m.WorkersProviders(N(AI=None),lambda event:None)
    request=asyncio.get_running_loop().create_future()
    provider.requests.add(request); request.add_done_callback(provider.requests.discard)
    async def existing():
        try: await asyncio.Event().wait()
        except asyncio.CancelledError:
            request.set_result(N(webSocket=JsNull(),body=body))
            await asyncio.sleep(0); await asyncio.sleep(0)
    provider._task(existing()); await asyncio.sleep(0)
    closing=asyncio.create_task(provider.close())
    while not body.started: await asyncio.sleep(0)
    assert body.started and provider.diagnostics()['provider_tasks']==1
    body.gate.set(); await closing; await asyncio.sleep(0)
    assert provider.diagnostics()['provider_tasks']==0

    # Disposal queued immediately before close must still invoke cancellation.
    body=Reader([]); completed=asyncio.get_running_loop().create_future()
    completed.set_result(N(webSocket=JsNull(),body=body))
    provider=m.WorkersProviders(N(AI=None),lambda event:None)
    provider._discard_late_result(completed)
    await provider.close()
    assert body.canceled and provider.diagnostics()['provider_tasks']==0

    ws=BrokenAccept(); completed=asyncio.get_running_loop().create_future()
    completed.set_result(N(webSocket=ws)); provider._discard_when_done(completed)
    provider._discard_when_done(completed)
    assert len(provider._discarding_requests)==1
    await asyncio.sleep(0)
    assert ws.closed and not provider._discarding_requests

asyncio.run(main())
