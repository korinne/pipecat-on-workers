"""Actual Worker access routing with fake SDK I/O; never loads real credentials."""
import asyncio
import importlib.util
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse
import sys
import types
from types import SimpleNamespace as N

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def stub(name, **attributes):
    module = types.ModuleType(name)
    module.__dict__.update(attributes)
    sys.modules[name] = module


class Base:
    def __init__(self, ctx=None, env=None): self.ctx, self.env = ctx, env


class Response:
    def __init__(self, body=None, *, status=200, headers=None, **kwargs):
        self.body, self.status, self.headers = body, status, headers or {}
        self.web_socket = kwargs.get("web_socket")


class Request:
    def __init__(self, url, *, method="GET", headers=None, body=None):
        self.url, self.method, self.headers, self.body = url, method, headers or {}, body
    async def text(self): return self.body or ""
    async def json(self): return json.loads(await self.text())


stub("js", WebSocketPair=N())
stub("pyodide", __version__="fake-test-runtime")
stub("pyodide.ffi", create_proxy=lambda callback: callback)
stub("workers", WorkerEntrypoint=Base, DurableObject=Base, Response=Response, Request=Request)
stub("loguru", logger=N(remove=lambda: None, add=lambda *a, **k: None))
stub("conversation", ConversationSession=N)
stub("providers", WorkersProviders=N, ProviderConnectionError=RuntimeError)
spec = importlib.util.spec_from_file_location("entry_auth_under_test", Path(__file__).resolve().parents[1] / "src/entry.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)


class Conversations:
    def __init__(self): self.lookups = 0; self.initializations = 0
    def idFromName(self, name): self.lookups += 1; return name
    def get(self, name): return self
    async def fetch(self, request):
        assert request.url == "https://conversation/init" and request.method == "POST"
        self.initializations += 1
        return Response("{}")


async def main():
    session_ids, session_tokens = set(), set()
    checks = 0
    # An old configured secret or stale header must not restore the removed gate.
    for environment in ({}, {"DEMO_ACCESS_KEY": "unused-legacy-secret"}):
        for supplied in (None, "old-client-value"):
            conversations = Conversations()
            env = N(**environment, CONVERSATIONS=conversations,
                    REALTIME_SFU_APP_ID="test-app", REALTIME_SFU_APP_SECRET="test-secret")
            worker = entry.Default(None, env)
            headers = {} if supplied is None else {"X-Demo-Key": supplied}
            response = await worker.fetch(Request("https://voice.example/api/access", method="POST", headers=headers))
            assert response.status == 200 and json.loads(response.body) == {"ok": True}
            assert conversations.lookups == 0
            checks += 1
            for transport in ("websocket", "webrtc"):
                response = await worker.fetch(Request("https://voice.example/api/session", method="POST",
                    headers=headers, body=json.dumps({"transport": transport})))
                assert response.status == 201
                assert response.headers["Cache-Control"] == "no-store"
                result = json.loads(response.body)
                assert result["transport"] == transport
                assert len(result["id"]) == 32 and len(result["token"]) >= 32
                assert result["id"] not in session_ids and result["token"] not in session_tokens
                session_ids.add(result["id"]); session_tokens.add(result["token"])
                assert "unused-legacy-secret" not in response.body
                checks += 1
            assert conversations.initializations == 2
            for body in ("[]", "invalid-json", "x" * 1025, '{"transport":"unknown"}'):
                response = await worker.fetch(Request("https://voice.example/api/session", method="POST", body=body))
                assert response.status == 400
                assert conversations.initializations == 2
                checks += 1

    # Public session creation does not expose an existing call's controls or data.
    token = "synthetic-private-session-token"
    obj = entry.Conversation(N(), N(ENABLE_TEST_ROUTES="false"))
    obj.state = {"id": "a" * 32, "token_hash": hashlib.sha256(token.encode()).hexdigest(), "status": "created"}
    base = "https://voice.example/api/session/" + "a" * 32
    for suffix in ("", "/diagnostics", "/probe", "/restart", "/sfu"):
        for headers in ({}, {"X-Session-Token": "wrong-session-token"}, {"X-Demo-Key": token}):
            result = await obj.fetch(Request(base+suffix, method="POST" if suffix in ("/restart", "/sfu") else "GET", headers=headers))
            assert result.status == 403, (suffix, headers.keys(), result.status)
            assert token not in result.body
            checks += 1
    for headers in ({"X-Session-Token": token}, {}):
        url = base + "/diagnostics" + ("?token=" + token if not headers else "")
        result = await obj.fetch(Request(url, headers=headers))
        assert result.status == 200
        assert json.loads(result.body) == {
            "live": False, "status": "created", "sfu_retired_connections": 0,
            "sfu_cleanup_records": [], "sfu_cleanup_persistence_failed": False,
        }
        assert token not in result.body
        checks += 1
    for suffix in ("/probe", "/restart"):
        result = await obj.fetch(Request(base+suffix, method="POST", headers={"X-Session-Token": token}))
        assert result.status == 426  # test routes remain unavailable in production
        checks += 1
    assert not obj.authorized(Request(base, headers={"X-Session-Token": next(iter(session_tokens))}), urlparse(base))
    checks += 1
    print(f"PASS: {checks} keyless creation, validation, unique capability, private session access and disabled fixture-route cases (fake I/O).")


if __name__ == "__main__": asyncio.run(main())
