#!/usr/bin/env python3
"""Offline, read-only capability audit. Requires Python 3.11+ for this runner."""
from __future__ import annotations

import argparse
import collections
import datetime
import email
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib

REPO_SHA = "6c17c0805f13f7609ba0a93ea8bf4c945797de18"
ARCHIVE_SHA = "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04"
MANIFEST_SHA = "a76b7916dd89a86262c5be6d45ca78fa16bc10e4a67ad84ad08d1a2e6f4b39db"
GUARD_SHA = "93952c380f6310d245f52fd81d46f9753d26bcc66ff77bf2ab02263aec11723b"
VERSION = "1.11.0"
STATUSES = {"supported_in_scope", "observed_gap", "untested", "test_error"}
PATH_INSERT = 'sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))'
FORBIDDEN_MARKER = "RuntimeError: Unsupported dependency imported in narrow configuration: audioop"
THREAD_MARKER = "RuntimeError: OS thread creation is forbidden in this compatibility check"
SCOPE = "Local CPython; synthetic frames; no Workers runtime, network, codecs, providers, or physical playback."
GUARD_LIMIT = "The guard denies selected imports and threading.Thread.start; it is not an exhaustive native-code or thread sandbox."


def sha(data):
    return hashlib.sha256(data).hexdigest()


def result(identifier, domain, status, claim, evidence=None, limitations=None):
    if status not in STATUSES:
        raise ValueError(status)
    return {"id": identifier, "domain": domain, "status": status, "claim": claim,
            "evidence": evidence or {}, "limitations": limitations or []}


def command(args, cwd, timeout=45):
    """No shell, no network setup, no installs. Suppress Python writes to targets."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Core assertions are evidence; inherited optimization must not strip them.
    env.pop("PYTHONOPTIMIZE", None)
    started = time.monotonic()
    try:
        completed = subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                                   capture_output=True, text=True, timeout=timeout)
        return {"command": [str(x) for x in args], "returncode": completed.returncode,
                "stdout": completed.stdout[-24000:], "stderr": completed.stderr[-24000:],
                "timed_out": False, "elapsed_seconds": round(time.monotonic() - started, 3)}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": [str(x) for x in args], "returncode": None,
                "stdout": "", "stderr": str(exc),
                "timed_out": isinstance(exc, subprocess.TimeoutExpired),
                "elapsed_seconds": round(time.monotonic() - started, 3), "execution_error": type(exc).__name__}


def strict_success(run):
    """An exit code alone is insufficient; require the guard's asserted report."""
    if run.get("execution_error") or run.get("timed_out") or run.get("returncode") != 0:
        return False
    try:
        body = json.loads(run["stdout"])
    except (KeyError, ValueError):
        return False
    return (body.get("passed") is True and body.get("generations") == 2
            and body.get("threads_created") == 0
            and body.get("forbidden_imports_loaded") == []
            and body.get("live_pipecat_tasks_after_cancel") == [])


def classify_ablation(run, expected_terminal_error):
    """Expected incompatibility is a gap, never a capability success."""
    if run.get("timed_out") or run.get("execution_error") or run.get("returncode") in (None, 0):
        return "test_error"
    # Match a complete exception line, not a source-code excerpt or arbitrary substring.
    if expected_terminal_error in run.get("stderr", "").splitlines():
        return "observed_gap"
    return "test_error"


def classify_candidate(run):
    if strict_success(run):
        return "supported_in_scope"
    if run.get("execution_error") or run.get("timed_out") or run.get("returncode") in (None, 0):
        return "test_error"
    errors = run.get("stderr", "").splitlines()
    if any(line.startswith("RuntimeError: Unsupported dependency imported in narrow configuration: ")
           or line == THREAD_MARKER
           or line.startswith("AssertionError") for line in errors):
        return "observed_gap"
    # A missing dependency, unrelated API change, or arbitrary crash is not proof
    # that a specifically claimed capability is unavailable.
    return "test_error"


def interpreter_details(run):
    if run.get("execution_error") or run.get("returncode") != 0:
        raise ValueError("Interpreter preflight did not complete.")
    details = json.loads(run["stdout"])
    if details.get("optimize") != 0:
        raise ValueError("Interpreter assertions are disabled or optimization status is unknown.")
    return details


def verify_repository(repo):
    head = command(["git", "rev-parse", "HEAD"], repo)
    dirty = command(["git", "status", "--porcelain", "--untracked-files=no"], repo)
    errors = []
    if head["returncode"] != 0 or head["stdout"].strip() != REPO_SHA:
        errors.append("Repository HEAD does not match the audited commit.")
    if dirty["returncode"] != 0 or dirty["stdout"].strip():
        errors.append("Tracked worktree files differ from the audited commit or git status failed.")
    manifest_path = repo / "src/pipecat/VENDOR_MANIFEST.json"
    if sha(manifest_path.read_bytes()) != MANIFEST_SHA:
        errors.append("Vendor manifest hash mismatch.")
    if sha((repo / "scripts/check_pipecat_core.py").read_bytes()) != GUARD_SHA:
        errors.append("Core guard hash mismatch.")
    manifest = json.loads(manifest_path.read_text())
    if manifest["version"] != VERSION or manifest["archive_sha256"] != ARCHIVE_SHA:
        errors.append("Vendor source identity mismatch.")
    patches = {p["file"]: p for p in manifest["patches"]}
    files = {p.relative_to(repo / "src/pipecat").as_posix(): p
             for p in (repo / "src/pipecat").rglob("*.py")}
    hashes = manifest["upstream_file_sha256"]
    if set(files) != set(hashes):
        errors.append("Vendor Python module set differs from manifest.")
    for name, path in files.items():
        expected = patches[name]["patched_sha256"] if name in patches else hashes.get(name)
        if sha(path.read_bytes()) != expected:
            errors.append("Vendor source hash mismatch: " + name)
    for name, patch in patches.items():
        if patch["upstream_sha256"] != hashes[name]:
            errors.append("Inconsistent original hash in patch record: " + name)
    if len(files) != manifest["python_files"] or sum(p.stat().st_size for p in files.values()) != manifest["python_source_bytes"]:
        errors.append("Vendor file count or source byte count differs from manifest.")
    evidence = {"repository_head": head["stdout"].strip(), "expected_head": REPO_SHA,
                "manifest_sha256": MANIFEST_SHA, "vendor_python_files": len(files),
                "patches": manifest["patches"], "errors": errors}
    return manifest, result("provenance.repository", "source provenance",
        "test_error" if errors else "supported_in_scope",
        "The local tracked repository, vendor module set, and patch hashes match the pinned baseline.", evidence,
        ["This verifies source identity, not compatibility or package installation."])


def read_archive(path, manifest):
    data = path.read_bytes()
    if sha(data) != ARCHIVE_SHA:
        raise ValueError("Source archive SHA-256 mismatch; refusing to use it.")
    prefix = "pipecat_ai-1.11.0/"
    with tarfile.open(path, "r:gz") as archive:
        def read(name):
            member = archive.getmember(prefix + name)
            if not member.isfile():
                raise ValueError("Expected regular archive member: " + name)
            return archive.extractfile(member).read()
        originals = {name: read("src/pipecat/" + name)
                     for name in manifest["upstream_file_sha256"]}
        for name, content in originals.items():
            if sha(content) != manifest["upstream_file_sha256"][name]:
                raise ValueError("Upstream hash mismatch: " + name)
        metadata = email.message_from_bytes(read("PKG-INFO"))
        pyproject = tomllib.loads(read("pyproject.toml").decode())
    if metadata["Name"] != "pipecat-ai" or metadata["Version"] != VERSION:
        raise ValueError("Source distribution package metadata mismatch.")
    evidence = {"archive_sha256": ARCHIVE_SHA, "archive_source": str(path),
                "name": metadata["Name"], "version": metadata["Version"],
                "requires_python": metadata["Requires-Python"],
                "requires_dist": metadata.get_all("Requires-Dist", []),
                "base_dependencies": pyproject["project"]["dependencies"],
                "original_hashes_verified": len(originals)}
    return originals, evidence


def scratch_repo(repo, temporary):
    # Copy only relevant, tracked source. Ignore untracked Python files/modules.
    listing = command(["git", "ls-files", "-z"], repo)
    if listing["returncode"] != 0:
        raise ValueError("Cannot enumerate tracked source.")
    # git ls-files output can exceed command()'s log bound; obtain full list explicitly.
    paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=repo).decode().split("\0")
    for relative in filter(None, paths):
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or (repo / path).is_symlink():
            raise ValueError("Unsafe tracked path: " + relative)
        target = temporary / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(repo / path, target)
    return temporary


def guard_command(python, target):
    return command([python, "-B", target / "scripts/check_pipecat_core.py"], target)


def candidate_check(candidate_python, dist, guard, temporary):
    probe = temporary / "candidate_identity.py"
    probe.write_text('''import importlib.metadata as m, importlib.util as u, json, pathlib, sys
if sys.flags.optimize != 0: raise RuntimeError("Candidate assertions are disabled")
d=m.distribution(sys.argv[1])
s=u.find_spec("pipecat")
if s is None or not s.origin: raise RuntimeError("No pipecat package found")
p=pathlib.Path(s.origin).resolve()
owned={pathlib.Path(d.locate_file(f)).resolve() for f in (d.files or [])}
if p not in owned: raise RuntimeError("Installed distribution does not own imported pipecat")
direct=json.loads(d.read_text("direct_url.json") or "{}")
if direct.get("dir_info",{}).get("editable"): raise RuntimeError("Editable candidate rejected")
print(json.dumps({"name":d.metadata["Name"],"version":d.version,"module_file":str(p),"executable":sys.executable,"optimize":sys.flags.optimize,"requires_dist":d.requires or []}))
''')
    identity = command([candidate_python, "-I", "-B", probe, dist], temporary)
    if identity["returncode"] != 0:
        return result("candidate.core", "installed candidate / local CPython", "test_error",
                      "Candidate identity could not be verified; core acceptance was not run.", identity)
    details = interpreter_details(identity)
    package_file = Path(details["module_file"])
    if not package_file.is_absolute() or "site-packages" not in package_file.parts:
        return result("candidate.core", "installed candidate / local CPython", "test_error",
                      "Candidate is not a regular installed site-packages distribution.", details)
    if guard.count(PATH_INSERT) != 1:
        raise ValueError("Guard path injection changed; refusing candidate adaptation.")
    target = temporary / "candidate_guard.py"
    target.write_text(guard.replace(PATH_INSERT, "# Candidate imports come from isolated interpreter site-packages.", 1))
    run = command([candidate_python, "-I", "-B", target], temporary)
    status = classify_candidate(run)
    return result("candidate.core", "installed candidate / local CPython", status,
                  "An installed candidate is tested against the pinned core API profile without applying compatibility edits.",
                  {"identity": details, "run": run},
                  [SCOPE, GUARD_LIMIT, "Installation, resolver compatibility, Workers execution, and alternative APIs are not tested.",
                   "A missing enable_import_prewarm argument is a test_error/API-profile mismatch, not proof of missing thread-free capability.",
                   "Distribution ownership identifies the imported package; wheel/RECORD hashes and installed-file integrity are not verified."])


UNTESTED = [
    ("workers.selected_pipeline", "selected Workers AI / Pipecat voice pipeline", "Verify hosted Smart Turn coordination and the selected STT, LLM, TTS, output, and assistant-aggregator components on actual Workers; the guarded core is a smaller configuration."),
    ("candidate.artifact_integrity", "candidate package provenance", "Verify the release artifact checksum/signature and installed-file RECORD hashes. Distribution ownership alone does not establish an unchanged installation."),
    ("package.installability", "packaging", "Resolve and install the final package in Python Workers without copied source or local edits; importing an existing environment does not prove this."),
    ("workers.runtime", "deployed Python Durable Object", "Deploy the same candidate on a pinned Python/Pyodide/workerd runtime; run real turns, cancellation, concurrent sessions, reconnect, and cleanup."),
    ("sfu.live", "live WebRTC / SFU", "Measure actual bidirectional media, stop-to-silence, stale speech, reconnect, standard conversation context, and confirmed remote resource cleanup."),
    ("workers.performance", "deployed workload measurements", "Measure CPU time (not wall time), memory, copies, queue depth, sustained streams, burst input, loss, PCM conversion, and concurrent objects against actual configured runtime limits and target latency."),
    ("physical.audio", "physical browser microphone / speaker", "Use physical audio and aligned loopback recordings to measure intelligibility, echo, turn endings, and audible interruption; synthetic frame checks cannot substitute."),
]


def audit(args):
    repo = args.repo.resolve()
    records = []
    environment = {"runner_python": sys.version, "runner_executable": sys.executable,
                   "runner_optimization": sys.flags.optimize,
                   "core_python_requested": args.python, "platform": platform.platform(),
                   "pythonpath": os.environ.get("PYTHONPATH"), "repository": str(repo),
                   "network_requested": False, "repo_mutated": False}
    try:
        manifest, verification = verify_repository(repo)
        records.append(verification)
        if verification["status"] != "supported_in_scope":
            raise ValueError("Baseline provenance failed; execution stopped.")
        originals = None
        if args.archive:
            originals, archive_evidence = read_archive(args.archive.resolve(), manifest)
            records.append(result("provenance.archive", "published source distribution", "supported_in_scope",
                                  "Pinned public archive metadata and original file hashes match the vendor record.", archive_evidence,
                                  ["Archive is supplied locally; this run makes no freshness claim about later releases."]))
            records.append(result("package.baseline_dependencies", "published package metadata", "observed_gap",
                "The baseline distribution declares heavy dependencies independently of this narrow application's needs.",
                {"base_dependencies": archive_evidence["base_dependencies"]},
                ["Individual runtime availability and resolver behavior require a Workers installation test."]))
        else:
            records.append(result("provenance.archive", "published source distribution", "untested",
                                  "Supply --archive to verify upstream bytes, dependency metadata, and the audio-import ablation."))
        with tempfile.TemporaryDirectory(prefix="pipecat-gap-audit-") as folder:
            temporary = Path(folder)
            target = scratch_repo(repo, temporary / "baseline")
            guard_path = target / "scripts/check_pipecat_core.py"
            guard = guard_path.read_text()
            identity = command([args.python, "-B", "-c", "import sys,platform,json,importlib.metadata as m; print(json.dumps({'python':sys.version,'executable':sys.executable,'optimize':sys.flags.optimize,'platform':platform.platform(),'dependency_versions':{n:m.version(n) for n in ['loguru','attrs','docstring-parser','pydantic','typing-extensions']}}))"], temporary)
            environment["core_interpreter"] = identity
            environment["verified_core_interpreter"] = interpreter_details(identity)
            environment["subprocess_pythonoptimize"] = "removed; optimization level verified as zero"
            environment["declared_repository_environment"] = tomllib.loads((target / "pyproject.toml").read_text())["project"]
            environment["scope_warning"] = "The chosen local interpreter/dependencies may differ from repository pins; this audit is not a lockfile install or Python Workers execution."
            baseline = guard_command(args.python, target)
            baseline_ok = strict_success(baseline)
            records.append(result("core.baseline", "vendored Pipecat / local CPython",
                "supported_in_scope" if baseline_ok else "test_error",
                "The patched narrow core handles two synthetic turns, interruption, context, and task cleanup.", baseline, [SCOPE, GUARD_LIMIT]))
            if baseline_ok:
                if originals:
                    audio_path = target / "src/pipecat/audio/utils.py"
                    saved = audio_path.read_bytes()
                    audio_path.write_bytes(originals["audio/utils.py"])
                    run = guard_command(args.python, target)
                    audio_path.write_bytes(saved)
                    records.append(result("ablation.eager_audio", "upstream import behavior / local CPython",
                        classify_ablation(run, FORBIDDEN_MARKER),
                        "Restoring upstream audio imports violates the selected unsupported-import guard.", run,
                        ["This isolates import behavior; it does not measure actual codec execution."]))
                else:
                    records.append(result("ablation.eager_audio", "upstream import behavior", "untested", "A verified upstream archive was not supplied."))
                if guard.count("enable_import_prewarm=False") != 1:
                    raise ValueError("Unexpected prewarm guard shape.")
                guard_path.write_text(guard.replace("enable_import_prewarm=False", "enable_import_prewarm=True", 1))
                run = guard_command(args.python, target)
                guard_path.write_text(guard)
                records.append(result("ablation.prewarm", "startup behavior / local CPython",
                    classify_ablation(run, THREAD_MARKER),
                    "Enabling the existing prewarm path attempts prohibited OS thread creation.", run,
                    ["This exposes the behavior by removing the opt-out in the test; the vendor source remains otherwise unchanged."]))
            else:
                for label in ("eager_audio", "prewarm"):
                    records.append(result("ablation." + label, "local CPython", "untested",
                                          "Baseline guard did not pass; no causal ablation claim can be made."))
            if args.existing_tests:
                checks = [("python", [args.python, "-B", "-m", "unittest", "discover", "-s", "tests"]),
                          ("javascript", [args.node, "--test", *[str(p.relative_to(target)) for p in sorted(target.glob("public/*.test.mjs"))],
                                          *[str(p.relative_to(target)) for p in sorted(target.glob("scripts/*.test.mjs"))]])]
                for label, invocation in checks:
                    run = command(invocation, target, timeout=120)
                    # Require explicit nonempty test counts; "0 tests passed" is not acceptance.
                    count = re.search(r"Ran (\d+) tests?", run["stderr"]) if label == "python" else re.search(r"(?:#|ℹ) tests (\d+)", run["stdout"])
                    ok = run["returncode"] == 0 and count is not None and int(count.group(1)) > 0
                    records.append(result("regression." + label, "local mocked regression suite",
                        "supported_in_scope" if ok else "test_error", "Existing " + label + " regression assertions remain true.", run,
                        ["Mocks and intentional limitations are included. In particular, SFU tests expect absent assistant history; this is not an SFU capability pass."]))
            else:
                records.append(result("regression.existing", "local regression tests", "untested", "Use --existing-tests to run repository Python and JavaScript tests."))
            if args.candidate_python:
                records.append(candidate_check(args.candidate_python, args.candidate_dist, guard, temporary))
            else:
                records.append(result("candidate.core", "installed candidate / local CPython", "untested", "No installed candidate interpreter was supplied."))
        # Source findings tied to the pinned, verified commit; not asserted service deficiencies.
        records.extend([
            result("sfu.history", "application source", "observed_gap", "The SFU application path omits assistant replies from history.",
                   {"source": "src/conversation.py:84", "regression": "tests/test_sfu_conversation.py:71"},
                   ["This is an explicit application policy tied to missing playback confirmation, not proof the SFU cannot support another policy."]),
            result("sfu.interruption_cleanup", "application source", "observed_gap", "Transport clear awaits remote cleanup; latency is not independent of control-plane responses.",
                   {"source": "src/sfu_transport.py:496", "await": "await self._cleanup_retired()"},
                   ["No measured audible latency is claimed; browser clear happens earlier."]),
            result("sfu.cleanup_contract", "application source", "observed_gap", "Cleanup records failures and can retain remote resources without a guaranteed successful retry.",
                   {"source": "src/sfu_transport.py:516", "close": "src/sfu_transport.py:581"},
                   ["This is an application contract gap, not a measured remote leak or proof of an SFU service limitation."]),
        ])
    except Exception as exc:
        records.append(result("audit.execution", "audit infrastructure", "test_error", str(exc), {"exception": type(exc).__name__}))
    records.extend(result(identifier, domain, "untested", reason,
                          limitations=["Requires a separate implementation or live environment; never promoted from local mocks."])
                   for identifier, domain, reason in UNTESTED)
    return {"schema_version": 1, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "environment": environment, "readiness": "not_established",
            "readiness_reason": "Results are scoped observations. Live runtime, media, and performance gates remain untested.",
            "counts": dict(collections.Counter(item["status"] for item in records)), "results": records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable, help="Interpreter with repo core dependencies; runner itself is stdlib-only")
    parser.add_argument("--node", default="node")
    parser.add_argument("--existing-tests", action="store_true")
    parser.add_argument("--candidate-python", help="Isolated interpreter containing an installed candidate distribution; artifact integrity is a separate untested gate")
    parser.add_argument("--candidate-dist", default="pipecat-ai")
    args = parser.parse_args()
    # Preserve the read-only target contract even if a caller chooses a bad output path.
    if args.output.resolve().is_relative_to(args.repo.resolve()):
        parser.error("--output must be outside the target repository")
    report = audit(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output.resolve()), "readiness": report["readiness"], "counts": report["counts"]}))
    # 1 = expected observed gaps remain; 2 = infrastructure/assumption error; never 0 for readiness.
    return 2 if report["counts"].get("test_error") else 1


if __name__ == "__main__":
    sys.exit(main())
