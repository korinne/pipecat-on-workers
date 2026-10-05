"""Isolated live probe of copied, hash-verified application GPT provider files.

See docs/GPT-OSS.md for the temporary directory recipe. No production routes.
Only fixed synthetic prompts and safe provider summaries are returned.
"""
import asyncio
import json
import platform
import time
from urllib.parse import urlparse
from workers import Response, WorkerEntrypoint
from providers import WorkersProviders


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        case = urlparse(str(request.url)).path.strip("/") or "normal"
        if case not in ("normal", "one-token", "empty", "follow-up", "invalid-budget", "cancel-before-answer", "cancel-after-answer"):
            return Response("Unknown probe", status=404)
        if request.method != "POST":
            return Response("POST a fixed probe case", status=405)
        messages = [{"role": "system", "content": "Answer in one brief sentence."},
                    {"role": "user", "content": "What is two plus two?"}]
        if case == "empty":
            messages[-1]["content"] = "Return no answer text."
        elif case == "follow-up":
            messages = [{"role": "system", "content": "Answer in one brief sentence."},
                        {"role": "user", "content": "Remember that the blue folder holds the picnic list."},
                        {"role": "assistant", "content": "The picnic list is in the blue folder."},
                        {"role": "user", "content": "Which folder did I say?"}]
        provider = WorkersProviders(self.env, lambda _: None)
        budget = 1 if case == "one-token" else ("invalid-probe-value" if case == "invalid-budget" else 2048)
        generator = provider.generate(messages, max_tokens=budget)
        report = {"case": case, "python": platform.python_version(), "answer": "",
                  "remote_compute_canceled": "unverified"}
        started = time.monotonic()
        try:
            if case == "cancel-before-answer":
                pending = asyncio.create_task(anext(generator))
                await asyncio.sleep(0.01)
                pending.cancel()
                try:
                    await pending
                except asyncio.CancelledError:
                    report["cancellation_propagated"] = True
            elif case == "cancel-after-answer":
                report["answer"] = await anext(generator)
                await generator.aclose()
                report["cancellation_propagated"] = True
            else:
                async for text in generator:
                    report["answer"] += text
            report["outcome"] = "returned"
        except Exception as error:
            report["outcome"] = "exception"
            report["exception_type"] = type(error).__name__
            report["failure_outcome"] = getattr(error, "outcome", None)
        finally:
            await generator.aclose()
            report["generation"] = getattr(provider, "last_generation", None)
            report["resources_before_close"] = provider.diagnostics()
            await asyncio.wait_for(provider.close(), 3)
            # Observe ownership settlement, not remote compute or billing.
            if provider.requests:
                await asyncio.sleep(1)
            report["resources_after_close"] = provider.diagnostics()
            report["elapsed_seconds"] = time.monotonic() - started
        return Response(json.dumps(report), headers={"Content-Type": "application/json", "Cache-Control": "no-store"})
