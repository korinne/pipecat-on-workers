#!/usr/bin/env python3
"""Resolve the conflicting Pydantic requirements, without installing anything.

This projects the application's and published Pipecat's Pydantic requirements
onto a declared Python version. The normal pip resolver then checks those exact
active constraints. It does not attempt the full Workers dependency installation.
Requires packaging and pip in the probing environment.
"""
import argparse
import hashlib
import json
import pathlib
import platform
import subprocess
import sys
import tarfile
import tomllib
from datetime import datetime, timezone

from packaging.markers import default_environment
from packaging.requirements import Requirement


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=pathlib.Path, required=True)
    parser.add_argument("--app-pyproject", type=pathlib.Path, required=True)
    parser.add_argument("--target-python", default="3.14.7")
    parser.add_argument("--output", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists; preserve old evidence and select a new path")
    archive_hash = sha(args.archive.read_bytes())
    if archive_hash != "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04":
        parser.error("archive checksum mismatch")
    with tarfile.open(args.archive) as archive:
        package_data = archive.extractfile("pipecat_ai-1.11.0/pyproject.toml").read()
    app_data = args.app_pyproject.read_bytes()
    environment = default_environment()
    environment.update(python_full_version=args.target_python, python_version=".".join(args.target_python.split(".")[:2]))
    requirements = []
    for origin, data in (("application", app_data), ("pipecat-ai 1.11.0", package_data)):
        for declaration in tomllib.loads(data.decode())["project"]["dependencies"]:
            requirement = Requirement(declaration)
            if requirement.name.lower() != "pydantic":
                continue
            active = requirement.marker is None or requirement.marker.evaluate(environment)
            requirements.append({"origin": origin, "declaration": declaration, "active": active, "resolver_input": f"{requirement.name}{requirement.specifier}" if active else None})
    inputs = [item["resolver_input"] for item in requirements if item["active"]]
    command = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--disable-pip-version-check", "--index-url", "https://pypi.org/simple", *inputs]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    combined = completed.stdout + completed.stderr
    reproduced = completed.returncode != 0 and "ResolutionImpossible" in combined and "conflicting dependencies" in combined and "pydantic>=2.13" in combined
    report = {
        "schema": "task1-runtime-constraints-v1", "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "status": "observed_gap" if reproduced else "test_error",
        "classification": "application dependency pin conflict with published package metadata" if reproduced else "test limitation",
        "archive_sha256": archive_hash, "package_pyproject_sha256": sha(package_data),
        "application_pyproject_sha256": sha(app_data), "probe_sha256": sha(pathlib.Path(__file__).read_bytes()),
        "target_python": args.target_python, "local_python": sys.version, "local_platform": platform.platform(),
        "requirements": requirements, "command": command, "exit_code": completed.returncode,
        "stdout": completed.stdout, "stderr": completed.stderr,
        "limits": ["Only the exact Pydantic requirement projection was resolved; this is not a full Pipecat or Workers installation test.", "Target Python markers were evaluated before invoking pip. The resolver itself runs on the recorded local interpreter.", "No packages or application files were changed. Reconciling the pin needs review and testing on Python Workers.", "This conflict does not itself justify a new platform capability request."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": report["status"], "output": str(args.output), "resolver_exit_code": completed.returncode}))
    return 1 if reproduced else 2


if __name__ == "__main__":
    raise SystemExit(main())
