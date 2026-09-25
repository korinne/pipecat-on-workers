"""Actual DO lifecycle methods with fake SDK/provider I/O; no network or media."""
import asyncio
import importlib.util
from pathlib import Path
import sys
import types
from types import SimpleNamespace as N


def stub(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module
    return module


class Base:
    def __init__(self, ctx=None, env=None):
        self.ctx, self.env = ctx, env


class ConnectionError(RuntimeError):
    pass


stub('js', WebSocketPair=N())
stub('pyodide', __version__='fake-test-runtime')
stub('pyodide.ffi', create_proxy=lambda callback: callback)
stub('workers', WorkerEntrypoint=Base, DurableObject=Base, Response=N, Request=N)
stub('loguru', logger=N(remove=lambda: None, add=lambda *a, **k: None))
stub('conversation', ConversationSession=N)
stub('providers', WorkersProviders=N, ProviderConnectionError=ConnectionError)
spec = importlib.util.spec_from_file_location('entry_under_test', Path(__file__).resolve().parents[1] / 'src/entry.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class Socket:
    readyState = 1
    def __init__(self): self.messages, self.closes = [], []
    def send(self, message): self.messages.append(entry.json.loads(message))
    def close(self, code, reason): self.closes.append((code, reason)); self.readyState = 3


class Session:
    def __init__(self, mode):
        self.mode, self.closed, self.canceled = mode, False, False
        self.started = asyncio.Event()
        self.last_activity = self.began = entry.time.monotonic()
    async def start(self):
        self.started.set()
        if self.mode == 'reject': raise ConnectionError('Speech service is busy. Please try again shortly.')
        if self.mode == 'pending':
            try: await asyncio.Future()
            except asyncio.CancelledError: self.canceled = True; raise
    async def close(self, reason): self.closed = True; self.reason = reason
    def diagnostics(self): return {'closed': self.closed}


async def case(mode, stop=None, before_start=False):
    saved = []
    async def put(key, value): saved.append((key, value))
    obj = entry.Conversation(N(storage=N(put=put)), N())
    obj.state = {'status': 'created'}
    obj.incoming = asyncio.Queue(maxsize=128)
    session = obj.session = Session(mode)
    socket = obj.socket = Socket()
    obj.starting_connection = True
    obj.pump = asyncio.create_task(obj.run())
    if not before_start: await session.started.wait()
    if stop: obj.enqueue({'type': stop})
    await asyncio.wait_for(obj.pump, 0.5)
    assert session.closed and obj.session is None
    assert obj.startup is None and obj.incoming is None
    assert saved and obj.last_diagnostics['closed']
    return obj, session, socket


async def main():
    obj, session, socket = await case('reject')
    assert socket.closes == [(1000, 'provider_startup_rejected')]
    assert obj.state['status'] == 'ended'
    assert socket.messages == [{'type': 'error', 'message': 'Speech service is busy. Please try again shortly.', 'recoverable': False}]
    obj, session, socket = await case('pending', 'end')
    assert session.canceled and session.reason == 'ended'
    assert socket.closes == [(1000, 'ended')]
    assert socket.messages == [{'type': 'ended'}]
    obj, session, socket = await case('pending', 'disconnect')
    assert session.canceled and session.reason == 'disconnect'
    assert socket.closes == [(1012, 'disconnect')]
    assert obj.state['status'] == 'disconnected'
    obj, session, socket = await case('ready', 'end')
    assert not session.canceled and session.reason == 'ended'
    assert socket.closes == [(1000, 'ended')]
    for stop in ('end', 'disconnect'):
        obj, session, socket = await case('pending', stop, before_start=True)
        assert not session.started.is_set()
        assert session.reason == ('ended' if stop == 'end' else stop)
        assert not obj.starting_connection
    # Browser End is immediately followed by socket disconnect. The latter
    # must not cancel the startup task a second time during provider cleanup.
    class SlowCleanupSession(Session):
        def __init__(self):
            super().__init__('pending')
            self.cleaning = asyncio.Event()
            self.release = asyncio.Event()
            self.provider_closed = False
        async def start(self):
            self.started.set()
            try: await asyncio.Future()
            except asyncio.CancelledError:
                await self.close('startup_failed')
                raise
        async def close(self, reason):
            if self.closed: return
            self.closed = True
            self.cleaning.set()
            await self.release.wait()
            self.provider_closed = True
    async def put(*args): pass
    obj = entry.Conversation(N(storage=N(put=put)), N())
    obj.state = {'status': 'created'}
    obj.incoming = asyncio.Queue()
    session = obj.session = SlowCleanupSession()
    obj.socket = Socket()
    obj.starting_connection = True
    obj.pump = asyncio.create_task(obj.run())
    await session.started.wait()
    obj.enqueue({'type': 'end'})
    await session.cleaning.wait()
    obj.enqueue({'type': 'disconnect'})
    await asyncio.sleep(0)
    assert not obj.startup.done(), 'Second cancellation interrupted cleanup'
    session.release.set()
    await asyncio.wait_for(obj.pump, .5)
    assert session.provider_closed
    print('DO startup lifecycle checks passed: terminal provider rejection, End cancellation, disconnect cancellation, ready then End, pre-start End/disconnect, End then disconnect cleanup (7 cases; fake I/O).')


if __name__ == '__main__': asyncio.run(main())
