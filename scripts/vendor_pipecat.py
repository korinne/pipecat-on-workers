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
import zipfile

VERSION = "1.11.0"
COMMIT = "3dede06bec0b497bddfdcf047af7495ee9d0726e"
ARCHIVE_URL = f"https://files.pythonhosted.org/packages/source/p/pipecat-ai/pipecat_ai-{VERSION}.tar.gz"
ARCHIVE_SHA256 = "49e7532a1f035e8884c47448a06d998a1b7f0943d11eae9bc8600a9e749a2a04"
PROJECT = pathlib.Path(__file__).resolve().parents[1]
PUNKT_URL = "https://raw.githubusercontent.com/nltk/nltk_data/gh-pages/packages/tokenizers/punkt_tab.zip"
PUNKT_SHA256 = "e57f64187974277726a3417ca6f181ec5403676c717672eef6a748a7b20e0106"
PUNKT_FILES = {
    "abbrev_types.txt": "92a3e070f43d9b4c5534758ca40ad7343b04e7e29bfe0c2eb658a39445a4f779",
    "collocations.tab": "8e2da1225e4dd2cc9dba261ee231ccb134859e21b46006e7f472c5ee269af0cf",
    "ortho_context.tab": "4bbcca25ed3d3f06c02402abf8419b9f033b8adc06e7b482eca4e45f81a5dc4c",
    "sent_starters.txt": "f3f8535483e1dba487241b764945168123bca3209a9645e59acd1225dc76edac",
}
PUNKT_README_SHA256 = "d3251cae66a9359bd68c039e3a46172a05ca9df0b27dcdfa23ae594d68d27ec4"


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
        source = replace_once(source, '    import numpy as np\n    # Convert raw audio bytes to a NumPy array of int16 samples\n    audio_data = np.frombuffer(pcm_bytes, dtype=np.int16)\n\n    # Check the maximum absolute amplitude in the frame\n    max_value = np.abs(audio_data).max()\n\n    # If max value is lower than SPEAKING_THRESHOLD, consider it as silence\n    return max_value <= SPEAKING_THRESHOLD\n', '    import struct\n    return all(abs(sample[0]) <= SPEAKING_THRESHOLD\n               for sample in struct.iter_unpack("<h", pcm_bytes))\n', relative)
        changes.append("Use signed PCM16 little-endian samples for output silence detection without NumPy; empty audio is silent and -32768 is correctly non-silent.")
    elif relative == "processors/aggregators/llm_context.py":
        source = replace_once(source, "from PIL import Image\n", "", relative)
        source = replace_once(source, "                # Encode to JPEG\n", "                # Encode to JPEG\n                from PIL import Image\n", relative)
        changes.append("Import Pillow only when raw images need JPEG encoding.")
    elif relative == "transports/base_output.py":
        source = replace_once(source, 'from PIL import Image\n', '', relative)
        source = replace_once(source, '                    image = Image.frombytes(frame.format, frame.size, frame.image)\n', '                    from PIL import Image\n                    image = Image.frombytes(frame.format, frame.size, frame.image)\n', relative)
        source = replace_once(source, 'from pipecat.audio.dtmf.utils import load_dtmf_audio\n', '', relative)
        source = replace_once(source, '        for button in frame.buttons:\n            dtmf_audio = await load_dtmf_audio(button, sample_rate=self._sample_rate)\n            dtmf_audio_frame = OutputAudioRawFrame(\n                audio=dtmf_audio, sample_rate=self._sample_rate, num_channels=1\n            )\n            await self.write_audio_frame(dtmf_audio_frame)\n', '        raise NotImplementedError("DTMF audio is not included in the Workers voice configuration")\n', relative)
        source = replace_once(source, '            self._resampler = create_stream_resampler(clear_after_secs=None)\n', '            self._resampler = None  # Fixed-rate audio does not need native resampling.\n', relative)
        source = replace_once(source, '            resampled = await self._resampler.resample(\n                frame.audio, frame.sample_rate, self._sample_rate\n            )\n', '            resampled = frame.audio\n            if frame.sample_rate != self._sample_rate:\n                if self._resampler is None:\n                    self._resampler = create_stream_resampler(clear_after_secs=None)\n                resampled = await self._resampler.resample(frame.audio, frame.sample_rate, self._sample_rate)\n', relative)
        source = replace_once(source, '            self._audio_buffer.extend(await self._resampler.flush())\n', '            if self._resampler is not None:\n                self._audio_buffer.extend(await self._resampler.flush())\n', relative)
        source = replace_once(source, '            await self._resampler.reset()\n', '            if self._resampler is not None:\n                await self._resampler.reset()\n', relative)
        changes.append("Import Pillow only when resizing an output image; video remains outside the selected configuration.")
        changes.append("Delay native resampler creation until sample rates differ; preserve the upstream resampling path, flush and reset when instantiated.")
        changes.append("Reject audio DTMF explicitly because its tone assets and file/native helpers are outside the selected configuration.")
    elif relative == "services/tts_service.py":
        source = replace_once(source, '        self._resampler = create_stream_resampler()\n', '        self._resampler = None  # Instantiate only when a provider changes sample rate.\n', relative)
        source = replace_once(source, '            if source_sample_rate and source_sample_rate != self.sample_rate:\n                return await self._resampler.resample(audio, source_sample_rate, self.sample_rate)\n', '            if source_sample_rate and source_sample_rate != self.sample_rate:\n                if self._resampler is None:\n                    self._resampler = create_stream_resampler()\n                return await self._resampler.resample(audio, source_sample_rate, self.sample_rate)\n', relative)
        changes.append("Delay native resampler creation until the provider audio rate differs from the TTS rate.")
    elif relative == "utils/string.py":
        source = replace_once(source, "    The tokenizer and its ``punkt_tab`` data load on first use, and the data is\n    downloaded if it isn't already present. Deployments that build their own\n    image should bundle it at build time (``python -m nltk.downloader\n    punkt_tab``) or point ``NLTK_DATA`` at a directory that already has it, so\n    that a slow or unavailable network can't delay the first bot turn.\n", '    The English ``punkt_tab`` tables are pinned to the Task 1 reference and\n    bundled in a generated module. NLTK interprets them on first use without\n    runtime data downloads or filesystem access. See VENDOR_MANIFEST.json.\n', relative)
        source = replace_once(source, 'import threading\n', '', relative)
        source = replace_once(source, 'from loguru import logger\n', '', relative)
        source = replace_once(source, '_load_lock = threading.Lock()\n', '', relative)
        source = replace_once(source, 'def _sent_tokenizer() -> Callable[[str], list[str]]:\n    """Load NLTK\'s sentence tokenizer and its ``punkt_tab`` data.\n\n    NLTK reaches scikit-learn through its optional classifier backends, so\n    importing it costs a few hundred milliseconds. Loading it here rather than\n    at module import keeps that cost off the startup path of pipelines that\n    never tokenize.\n\n    A caller arriving while the pipeline\'s background warming is still loading\n    waits on the lock rather than loading alongside it, so the one-time\n    ``punkt_tab`` download cannot run twice at once. The cache keeps the lock\n    off the path once the tokenizer is loaded.\n    """\n    with _load_lock:\n        import nltk\n        from nltk.tokenize import sent_tokenize\n\n        try:\n            nltk.data.find("tokenizers/punkt_tab")\n        except LookupError:\n            try:\n                nltk.download("punkt_tab", quiet=True)\n            except (OSError, PermissionError) as e:\n                logger.error(\n                    f"Failed to download NLTK \'punkt_tab\' tokenizer data: {e}. "\n                    "This data is required for sentence tokenization features. "\n                    "The download failed due to filesystem permissions. "\n                    "To resolve: pre-install the data in a location with appropriate read "\n                    "permissions, or set the NLTK_DATA environment variable to point to a "\n                    "writable directory. See https://www.nltk.org/data.html for more information."\n                )\n\n        return sent_tokenize', 'def _sent_tokenizer() -> Callable[[str], list[str]]:\n    """Load the normal NLTK tokenizer with bundled English reference tables."""\n    from pipecat.utils.text.english_punkt import sentence_tokenize\n    return sentence_tokenize', relative)
        changes.append("Use normal NLTK with checksum-pinned English Punkt tables bundled as a generated module; remove runtime corpus lookup/download.")
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


def punkt_module(archive: bytes) -> tuple[str, bytes, dict]:
    """Package the unchanged reference English tables for Workers module loading."""
    digest = hashlib.sha256(archive).hexdigest()
    if digest != PUNKT_SHA256:
        raise RuntimeError(f"Punkt archive checksum mismatch: expected {PUNKT_SHA256}, got {digest}")
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        data = {}
        for name, expected in PUNKT_FILES.items():
            raw = zipped.read(f"punkt_tab/english/{name}")
            if hashlib.sha256(raw).hexdigest() != expected:
                raise RuntimeError(f"Punkt table checksum mismatch: {name}")
            data[name] = raw.decode("utf-8")
        readme = zipped.read("punkt_tab/README")
    if hashlib.sha256(readme).hexdigest() != PUNKT_README_SHA256:
        raise RuntimeError("Punkt README checksum mismatch")
    template = (PROJECT / "scripts" / "english_punkt.py.in").read_text()
    tables = "{\n" + "".join(f"    {name!r}: {value!r},\n" for name, value in data.items()) + "}"
    source = replace_once(template, "__PUNKT_DATA__", tables, "English Punkt template")
    ast.parse(source)
    return source, readme, {
        "archive_url": PUNKT_URL, "archive_sha256": digest,
        "table_sha256": PUNKT_FILES, "readme_sha256": PUNKT_README_SHA256,
        "template_sha256": hashlib.sha256(template.encode("utf-8")).hexdigest(),
    }


def vendor(archive: bytes, destination: pathlib.Path, *, punkt_archive: bytes, all_python: bool = False) -> dict:
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
    punkt_source, punkt_readme, punkt_provenance = punkt_module(punkt_archive)
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
    local_path = "utils/text/english_punkt.py"
    (temporary / local_path).write_text(punkt_source, encoding="utf-8")
    (temporary / "PUNKT-README.txt").write_bytes(punkt_readme)
    total += len(punkt_source.encode("utf-8"))
    (temporary / "LICENSE").write_bytes(license_bytes)
    manifest = {
        "name": "pipecat-ai", "version": VERSION, "commit": COMMIT,
        "archive_url": ARCHIVE_URL, "archive_sha256": digest,
        "python_files": len(extracted) + 1, "python_source_bytes": total,
        "policy": ("All upstream Python modules" if all_python else "Pinned module allowlist for the core and standard TTS/output/assistant flow; unrelated provider/transport implementations and local inference assets omitted.") + " Local compatibility edits and bundled English Punkt tables remain a vendored prototype, not a supported package installation.",
        "local_additions": [{"file": local_path, "sha256": hashlib.sha256(punkt_source.encode("utf-8")).hexdigest(), "purpose": "Load unchanged English Punkt tables through normal NLTK without runtime data files or downloads.", "provenance": punkt_provenance}],
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
    parser.add_argument("--punkt-archive", type=pathlib.Path, help="Checksum-pinned punkt_tab.zip; downloaded when omitted")
    parser.add_argument("--all-python", action="store_true", help="Research only: vendor every Python module to regenerate the exercised allowlist")
    args = parser.parse_args()
    archive = args.archive.read_bytes() if args.archive else urllib.request.urlopen(ARCHIVE_URL, timeout=60).read()
    punkt_archive = args.punkt_archive.read_bytes() if args.punkt_archive else urllib.request.urlopen(PUNKT_URL, timeout=60).read()
    manifest = vendor(archive, args.destination, punkt_archive=punkt_archive, all_python=args.all_python)
    print(json.dumps({key: manifest[key] for key in ("version", "commit", "archive_sha256", "python_files", "python_source_bytes", "patches")}, indent=2))


if __name__ == "__main__":
    main()
