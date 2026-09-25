#!/usr/bin/env python3
"""Vendor pinned real Pipecat source with auditable Workers-only compatibility edits.

This does not reimplement the pipeline. Native audio, model, transport, and image
features remain unsupported in the narrow Workers configuration. Run with Python
3.11+; only the standard library is needed. Use --archive for an offline build.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import pathlib
import shutil
import tarfile
import urllib.request

VERSION = "1.11.0"
COMMIT = "3dede06bec0b497bddfdcf047af7495ee9d0726e"
ARCHIVE_URL = f"https://files.pythonhosted.org/packages/source/p/pipecat-ai/pipecat_ai-{VERSION}.tar.gz"
ARCHIVE_SHA256 = "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04"
PROJECT = pathlib.Path(__file__).resolve().parents[1]


def replace_once(source: str, before: str, after: str, label: str) -> str:
    count = source.count(before)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact source match, found {count}")
    return source.replace(before, after, 1)


def defer_imports(source: str, imports: dict[str, str], label: str) -> str:
    """Move verified module imports into functions which actually use them.

    This deliberately handles only top-level functions, with no annotations or
    module-level uses of the relocated symbols. Fail rather than guessing if a
    future upstream change changes this shape.
    """
    module = ast.parse(source)
    lines = source.splitlines(keepends=True)
    edits: list[tuple[int, int, list[str]]] = []
    for symbol, statement in imports.items():
        matching = [n for n in module.body if isinstance(n, (ast.Import, ast.ImportFrom))
                    and ast.get_source_segment(source, n) == statement]
        if len(matching) != 1:
            raise RuntimeError(f"{label}: import changed: {statement}")
        node = matching[0]
        edits.append((node.lineno - 1, node.end_lineno, []))
        consumers = []
        for top in module.body:
            if top is node:
                continue
            uses = [n for n in ast.walk(top) if isinstance(n, ast.Name) and n.id == symbol]
            if not uses:
                continue
            if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
                raise RuntimeError(f"{label}: {symbol} has a non-function use")
            # Annotations/defaults cannot be satisfied by an import in the body.
            signature_nodes = [top.args, *top.decorator_list]
            if top.returns is not None:
                signature_nodes.append(top.returns)
            if any(isinstance(n, ast.Name) and n.id == symbol
                   for signature in signature_nodes for n in ast.walk(signature)):
                raise RuntimeError(f"{label}: {symbol} used in a signature")
            first = top.body[0]
            position = first.end_lineno if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str) else first.lineno - 1
            consumers.append((position, statement))
        if not consumers:
            raise RuntimeError(f"{label}: no consumers for {symbol}")
        for position, local_import in consumers:
            edits.append((position, position, [f"    {local_import}\n"]))
    for start, end, replacement in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start:end] = replacement
    patched = "".join(lines)
    ast.parse(patched)
    return patched


def patch_file(relative: str, source: str) -> tuple[str, list[str]]:
    changes: list[str] = []
    if relative == "__init__.py":
        source = replace_once(source, '__version__ = lib_version("pipecat-ai")',
            f'__version__ = "{VERSION}"  # Source vendored at {COMMIT}; see VENDOR_MANIFEST.json.', relative)
        changes.append("Use the pinned source version without requiring installed distribution metadata.")
    elif relative == "audio/utils.py":
        source = defer_imports(source, {
            "audioop": "import audioop",
            "loudness": "import loudness",
            "np": "import numpy as np",
            "SOXRAudioResampler": "from pipecat.audio.resamplers.soxr_resampler import SOXRAudioResampler",
            "SOXRStreamAudioResampler": "from pipecat.audio.resamplers.soxr_stream_resampler import SOXRStreamAudioResampler",
        }, relative)
        changes.append("Defer native audio/resampling dependencies until their functions are used.")
    elif relative == "processors/aggregators/llm_context.py":
        source = replace_once(source, "from PIL import Image\n", "", relative)
        source = replace_once(source, "                # Encode to JPEG\n", "                # Encode to JPEG\n                from PIL import Image\n", relative)
        changes.append("Import Pillow only when raw images need JPEG encoding.")
    elif relative == "transports/base_output.py":
        source = replace_once(source, "from PIL import Image\n", "", relative)
        source = replace_once(source, "                    image = Image.frombytes(frame.format, frame.size, frame.image)\n", "                    from PIL import Image\n                    image = Image.frombytes(frame.format, frame.size, frame.image)\n", relative)
        changes.append("Import Pillow only when the output transport resizes an image; browser transport uses a custom FrameProcessor.")
    elif relative == "pipeline/worker.py":
        source = replace_once(source, "import asyncio\n", "from __future__ import annotations\n\nimport asyncio\n", relative)
        source = replace_once(source,
            "from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIObserverParams, RTVIProcessor\n", "", relative)
        source = replace_once(source,
            "        external_rtvi = self._find_processor(pipeline, RTVIProcessor)\n        external_observer_found = any(isinstance(o, RTVIObserver) for o in observers)\n",
            "        def uses_rtvi(obj):\n"
            "            # Preserve detection of explicitly supplied RTVI subclasses.\n"
            "            if any(cls.__module__.startswith('pipecat.processors.frameworks.rtvi')\n"
            "                   for cls in type(obj).__mro__):\n"
            "                return True\n"
            "            return any(uses_rtvi(child) for child in getattr(obj, 'processors', []))\n\n"
            "        external_rtvi = None\n"
            "        external_observer_found = False\n"
            "        if enable_rtvi or rtvi_processor is not None or uses_rtvi(pipeline) or any(uses_rtvi(o) for o in observers):\n"
            "            from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor\n"
            "            external_rtvi = self._find_processor(pipeline, RTVIProcessor)\n"
            "            external_observer_found = any(isinstance(o, RTVIObserver) for o in observers)\n", relative)
        source = replace_once(source, "        enable_rtvi: bool = True,\n", "        enable_rtvi: bool = True,\n        enable_import_prewarm: bool = True,\n", relative)
        source = replace_once(source, "        self._params = params or PipelineParams()\n", "        self._params = params or PipelineParams()\n        self._enable_import_prewarm = enable_import_prewarm\n", relative)
        source = replace_once(source, "            await asyncio.to_thread(warm_deferred_imports)\n", "            if self._enable_import_prewarm:\n                await asyncio.to_thread(warm_deferred_imports)\n", relative)
        changes.append("Add opt-out enable_import_prewarm=False; preserve upstream default and avoid unconditional startup threads.")
        changes.append("Defer RTVI processor/observer imports when RTVI is disabled; keep detection of explicit RTVI classes through their MRO.")
    elif relative in ("processors/frameworks/rtvi/__init__.py", "turns/user_start/__init__.py"):
        optional_krisp = relative == "turns/user_start/__init__.py"
        if optional_krisp:
            source = replace_once(source, "try:\n    from .krisp_viva_ip_user_turn_start_strategy import KrispVivaIPUserTurnStartStrategy\nexcept ImportError:\n    KrispVivaIPUserTurnStartStrategy = None  # krisp_audio not installed\n", "", relative)
        tree = ast.parse(source)
        module_imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
        exported = {}
        for node in module_imports:
            for name in node.names:
                package = "pipecat." + relative.removesuffix("/__init__.py").replace("/", ".")
                module = package + "." + node.module if node.level == 1 else node.module
                if node.level > 1 or name.name == "*":
                    raise RuntimeError(f"{relative}: unexpected export structure")
                exported[name.asname or name.name] = (module, name.name)
        for node in sorted(module_imports, key=lambda n: n.lineno, reverse=True):
            before = ast.get_source_segment(source, node)
            source = replace_once(source, before, "", relative)
        if optional_krisp:
            exported["KrispVivaIPUserTurnStartStrategy"] = ("pipecat.turns.user_start.krisp_viva_ip_user_turn_start_strategy", "KrispVivaIPUserTurnStartStrategy")
        source += "\n\n# Keep frame/model imports independent of optional transports and services.\n"
        source += "_LAZY_EXPORTS = " + repr(exported) + "\n\n"
        source += "def __getattr__(name):\n    import importlib\n    if name not in _LAZY_EXPORTS:\n        raise AttributeError(name)\n    module, attribute = _LAZY_EXPORTS[name]\n    try:\n        value = getattr(importlib.import_module(module), attribute)\n    except ImportError:\n        if name != 'KrispVivaIPUserTurnStartStrategy':\n            raise\n        value = None\n    globals()[name] = value\n    return value\n"
        changes.append("Resolve unchanged public exports lazily so unused optional features do not eagerly load native transports, NumPy, or provider SDKs.")
    return source, changes


def vendor(archive: bytes, destination: pathlib.Path, *, all_python: bool = False) -> dict:
    digest = hashlib.sha256(archive).hexdigest()
    if digest != ARCHIVE_SHA256:
        raise RuntimeError(f"Pipecat archive checksum mismatch: expected {ARCHIVE_SHA256}, got {digest}")
    extracted: dict[str, bytes] = {}
    license_bytes = None
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = pathlib.PurePosixPath(member.name).parts
            if not member.isfile() or ".." in parts or not parts or parts[0] != f"pipecat_ai-{VERSION}":
                continue
            if parts[1:] == ("LICENSE",):
                license_bytes = tar.extractfile(member).read()
            if parts[1:3] == ("src", "pipecat") and member.name.endswith(".py"):
                relative = "/".join(parts[3:])
                extracted[relative] = tar.extractfile(member).read()
    if not license_bytes or "pipeline/worker.py" not in extracted:
        raise RuntimeError("Archive does not contain expected Pipecat source and license")
    if not all_python:
        allowlist_path = PROJECT / "scripts" / "pipecat_modules.txt"
        allowlist = {line.strip() for line in allowlist_path.read_text().splitlines()
                     if line.strip() and not line.startswith("#")}
        missing = allowlist.difference(extracted)
        if missing:
            raise RuntimeError(f"Module allowlist is not present in pinned upstream: {sorted(missing)}")
        extracted = {name: source for name, source in extracted.items() if name in allowlist}
    temporary = destination.with_name(destination.name + ".building")
    if temporary.exists():
        shutil.rmtree(temporary)
    temporary.mkdir(parents=True)
    patches = []
    total = 0
    source_hashes = {}
    for relative, data in sorted(extracted.items()):
        source_hashes[relative] = hashlib.sha256(data).hexdigest()
        source, changes = patch_file(relative, data.decode("utf-8"))
        ast.parse(source)
        path = temporary / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
        total += len(source.encode("utf-8"))
        if changes:
            patches.append({"file": relative, "changes": changes,
                "upstream_sha256": source_hashes[relative],
                "patched_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()})
    (temporary / "LICENSE").write_bytes(license_bytes)
    manifest = {
        "name": "pipecat-ai", "version": VERSION, "commit": COMMIT,
        "archive_url": ARCHIVE_URL, "archive_sha256": digest,
        "python_files": len(extracted), "python_source_bytes": total,
        "policy": "All upstream Python modules" if all_python else "Module allowlist exercised by the guarded core runtime check; unrelated transports/providers and all model/data assets omitted.",
        "patches": patches, "upstream_file_sha256": source_hashes,
    }
    (temporary / "VENDOR_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n")
    if destination.exists():
        shutil.rmtree(destination)
    temporary.rename(destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=pathlib.Path, help="Pinned source tar.gz already on disk")
    parser.add_argument("--destination", type=pathlib.Path, default=PROJECT / "src" / "pipecat")
    parser.add_argument("--all-python", action="store_true", help="Research only: vendor every Python module to regenerate the exercised allowlist")
    args = parser.parse_args()
    archive = args.archive.read_bytes() if args.archive else urllib.request.urlopen(ARCHIVE_URL, timeout=60).read()
    manifest = vendor(archive, args.destination, all_python=args.all_python)
    print(json.dumps({key: manifest[key] for key in ("version", "commit", "archive_sha256", "python_files", "python_source_bytes", "patches")}, indent=2))


if __name__ == "__main__":
    main()
