"""Controlled baseline parser probes; no model, Pipecat, or Workers is run.

The JS stand-ins below test the unchanged application adapter. They are not a
package/import workaround and must not be used to score Workers support.
"""
import argparse
import asyncio
import hashlib
import importlib.util
import json
import pathlib
import platform
import subprocess
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace as N

ROOT = pathlib.Path(__file__).resolve().parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_provider():
    js = types.ModuleType("js")
    js.Object = N(fromEntries=lambda value: value)
    js.Uint8Array = N(new=lambda value: N(to_py=lambda: memoryview(value)))
    ffi = types.ModuleType("pyodide.ffi")
    ffi.to_js = lambda value, **kwargs: value
    ffi.create_proxy = lambda value: value
    sys.modules.update({"js": js, "pyodide": types.ModuleType("pyodide"), "pyodide.ffi": ffi})
    spec = importlib.util.spec_from_file_location("task1_actual_providers", ROOT / "src/providers.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Reader:
    __hash__ = None

    def __init__(self, chunks, hold=False):
        self.chunks = list(chunks)
        self.hold = hold
        self.started = asyncio.Event()
        self.canceled = False
        self.released = False

    async def read(self):
        self.started.set()
        if self.hold:
            await asyncio.Event().wait()
        await asyncio.sleep(0)
        return N(done=False, value=self.chunks.pop(0)) if self.chunks else N(done=True)

    async def cancel(self, *args):
        self.canceled = True

    def releaseLock(self):
        self.released = True


class AI:
    def __init__(self, reader):
        self.reader = reader
        self.calls = []

    async def run(self, *args):
        self.calls.append(args)
        return N(getReader=lambda: self.reader)


def sse(events, done=True):
    body = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\r\n\r\n" for event in events)
    if done:
        body += "data: [DONE]\r\n\r\n"
    data = body.encode()
    # Every Unicode codepoint and SSE delimiter can span a transport read.
    return [data[i:i + 1] for i in range(len(data))]


async def run():
    module = load_provider()
    cases = json.loads((ROOT / "audit/reference/gpt-fixtures.json").read_text())
    results = []
    request = None
    for case in cases:
        reader = Reader(sse(case["events"], case.get("done", True)))
        ai = AI(reader)
        provider = module.WorkersProviders(N(AI=ai), lambda event: None)
        output, failure = [], None
        try:
            async for delta in provider.generate([{"role": "user", "content": "Controlled fixture"}]):
                output.append(delta)
        except Exception as error:
            failure = type(error).__name__
        observed = {"text": "".join(output), "exception_type": failure,
                    "reader_canceled": reader.canceled, "reader_released": reader.released,
                    "owned_readers_after": provider.diagnostics()["provider_readers"]}
        valid = (observed["text"] == case["expected_baseline_text"]
                 and failure == case.get("expected_baseline_exception")
                 and reader.canceled and reader.released and observed["owned_readers_after"] == 0)
        results.append({"id": case["id"], "status": case["finding_status"] if valid else "test_error",
                        "classification": case["classification"], "evidence_kind": "controlled_fixture",
                        "fixture_provenance": case["fixture_provenance"], "observation": observed,
                        "finding": case["finding"]})
        request = {"model": ai.calls[0][0], "inputs": ai.calls[0][1]}
        await provider.close()

    # Cancel while waiting for a first chunk: a task cancel must own reader cleanup.
    reader = Reader([], hold=True)
    provider = module.WorkersProviders(N(AI=AI(reader)), lambda event: None)
    generator = provider.generate([])
    pending = asyncio.create_task(generator.__anext__())
    await reader.started.wait()
    pending.cancel()
    try:
        await pending
    except asyncio.CancelledError:
        pass
    await generator.aclose()
    ok = reader.canceled and reader.released and provider.diagnostics()["provider_readers"] == 0
    results.append({"id": "cancel-before-answer", "status": "scoped_pass" if ok else "test_error",
                    "classification": "test_limitation", "evidence_kind": "controlled_fixture",
                    "observation": {"reader_canceled": reader.canceled, "reader_released": reader.released},
                    "finding": "Canceling the Python task closes the fake owned reader before any answer. This does not measure the real binding or remote compute."})
    await provider.close()

    reader = Reader(sse([{"choices": [{"delta": {"content": "first"}}]},
                         {"choices": [{"delta": {"content": "late"}}]}]))
    provider = module.WorkersProviders(N(AI=AI(reader)), lambda event: None)
    generator = provider.generate([])
    first = await generator.__anext__()
    await generator.aclose()
    remaining = [delta async for delta in generator]
    ok = first == "first" and not remaining and reader.canceled and reader.released
    results.append({"id": "cancel-after-answer-start", "status": "scoped_pass" if ok else "test_error",
                    "classification": "test_limitation", "evidence_kind": "controlled_fixture",
                    "observation": {"first_text": first, "late_text": remaining,
                                    "reader_canceled": reader.canceled, "reader_released": reader.released},
                    "finding": "Closing the generator suppresses its later fixture answer and releases its reader. Transport playback and remote compute are not exercised."})
    await provider.close()
    return {"task": 1, "readiness": "not_established", "evidence_kind": "controlled_fixture",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "runtime": {"python": sys.version, "platform": platform.platform(), "workers": False},
            "source_hashes": {name: digest(ROOT / name) for name in
                              ["src/providers.py", "audit/reference/gpt_probe.py", "audit/reference/gpt-fixtures.json"]},
            "actual_baseline_request": request,
            "selected_reference_request": {"model": "@cf/openai/gpt-oss-120b", "inputs": {
                "messages": [{"role": "system", "content": "Answer in one brief sentence."},
                             {"role": "user", "content": "What is two plus two?"}],
                "stream": True, "reasoning_effort": "low", "max_tokens": 2048}},
            "reference_request_status": "documented_source_shape_not_live_accepted",
            "budget_note": "2048 is a bounded initial probe allocation, not an established application budget. Verify reasoning-plus-answer accounting and token-limit behavior on the binding before implementation.",
            "results": results,
            "untested": ["Live Python binding request and FFI", "Actual GPT-OSS stream schema",
                         "Provider acceptance and effect of reasoning_effort=low", "Budget sufficiency and reasoning accounting",
                         "Real empty/error/length results", "Time to first speakable answer and audible audio",
                         "Remote inference or billing cancellation", "Context-dependent follow-up through both transports"]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Refusing to overwrite an evidence file; choose a new result path")
    result = asyncio.run(run())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    failed = [r["id"] for r in result["results"] if r["status"] == "test_error"]
    print(json.dumps({"result": str(args.output), "cases": len(result["results"]), "test_errors": failed}))
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
