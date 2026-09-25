"""Actual Worker access routing with fake SDK I/O; never loads real credentials."""
import asyncio
import importlib.util
import json
from pathlib import Path
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
    configured = "synthetic-test-key"
    scenarios = [
        ({"DEMO_ACCESS_KEY": configured}, None, 401, "access_key_required"),
        ({"DEMO_ACCESS_KEY": configured}, "", 401, "access_key_required"),
        ({"DEMO_ACCESS_KEY": configured}, "wrong-test-key", 401, "invalid_access_key"),
        ({"DEMO_ACCESS_KEY": configured}, "clé-incorrecte", 401, "invalid_access_key"),
        ({"DEMO_ACCESS_KEY": configured}, configured, 200, None),
        ({}, configured, 503, "access_not_configured"),
        ({"DEMO_ACCESS_KEY": ""}, None, 503, "access_not_configured"),
        ({"ALLOW_UNAUTHENTICATED_LOCAL": "true"}, None, 200, None),
        ({"ALLOW_UNAUTHENTICATED_LOCAL": "false"}, None, 503, "access_not_configured"),
        ({"DEMO_ACCESS_KEY": configured, "ALLOW_UNAUTHENTICATED_LOCAL": "true"}, "wrong-test-key", 401, "invalid_access_key"),
        ({"DEMO_ACCESS_KEY": "clé-test"}, "clé-test", 200, None),
    ]
    for environment, supplied, expected_status, code in scenarios:
        for endpoint in ("access", "session"):
            conversations = Conversations()
            env = N(**environment, CONVERSATIONS=conversations)
            worker = entry.Default(None, env)
            headers = {} if supplied is None else {"X-Demo-Key": supplied}
            response = await worker.fetch(Request(f"https://voice.example/api/{endpoint}", method="POST", headers=headers))
            status = 201 if endpoint == "session" and expected_status == 200 else expected_status
            assert response.status == status, (endpoint, code, response.status)
            result = json.loads(response.body)
            assert response.headers["Cache-Control"] == "no-store"
            if code:
                assert result["code"] == code and isinstance(result["error"], str)
                assert conversations.lookups == conversations.initializations == 0
                assert "DEMO_ACCESS_KEY" not in response.body
                for value in (supplied, environment.get("DEMO_ACCESS_KEY")):
                    if value: assert value not in response.body
            elif endpoint == "access":
                assert result == {"ok": True}
                assert conversations.lookups == conversations.initializations == 0
            else:
                assert len(result["id"]) == 32 and result["token"]
                assert conversations.lookups == conversations.initializations == 1
    print("PASS: 22 Worker access cases: missing/wrong/correct keys, non-ASCII input, missing configuration, local bypass, shared session guard, and access-only verification without conversation creation (fake I/O).")


if __name__ == "__main__": asyncio.run(main())
