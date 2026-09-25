"""Run a real-time synthetic Pipecat longevity test in local CPython.

Example: .venv/bin/python scripts/soak.py --seconds 600 --output evidence/soak-cpython.json
This is not an end-to-end voice or Cloudflare Durable Object test.
"""
import argparse
import asyncio
import hashlib
import json
import pathlib
import sys
import tracemalloc

project = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project / "src"))


def source_hashes():
    names = ["src/conversation.py", "src/runtime_probe.py", "src/pipecat/VENDOR_MANIFEST.json"]
    return {name: hashlib.sha256((project / name).read_bytes()).hexdigest() for name in names}


loaded_source_hashes = source_hashes()

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING")

tracemalloc.start()
from runtime_probe import run_soak


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=600)
    parser.add_argument("--output", type=pathlib.Path)
    parser.add_argument("--sessions", type=int, default=4)
    args = parser.parse_args()
    async def progress(sample):
        print(json.dumps({"progress": sample}), flush=True)

    def memory():
        current, peak = tracemalloc.get_traced_memory()
        return {"cpython_tracemalloc_current_bytes": current, "cpython_tracemalloc_peak_bytes": peak}

    result = await run_soak(args.seconds, sessions=args.sessions, progress=progress, memory_sample=memory)
    result["execution_environment"] = "local CPython, not Cloudflare Durable Object"
    result["memory_limitations"] = "tracemalloc began before Pipecat import; traced Python allocations only, not process RSS, WASM or isolate memory"
    result["source_sha256_at_start"] = loaded_source_hashes
    result["source_files_changed_during_run"] = loaded_source_hashes != source_hashes()
    body = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body)
    print(body)


if __name__ == "__main__":
    asyncio.run(main())
