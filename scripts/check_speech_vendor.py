#!/usr/bin/env python3
"""Verify vendored speech provenance and narrow compatibility on CPython.

This is a local source/import/tokenizer check, not Workers or live voice evidence.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import struct
import sys
import tempfile
import threading
import zipfile

from vendor_pipecat import PUNKT_FILES, vendor

ROOT = Path(__file__).resolve().parents[1]


def denied_thread(*args, **kwargs):
    raise AssertionError("The selected speech imports/tokenizer started an OS thread")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--punkt-archive", required=True, type=Path)
    args = parser.parse_args()
    source = ROOT / "src" / "pipecat"
    with tempfile.TemporaryDirectory(prefix="pipecat-speech-vendor-") as temporary:
        temporary = Path(temporary)
        regenerated = temporary / "pipecat"
        manifest = vendor(args.archive.read_bytes(), regenerated,
                          punkt_archive=args.punkt_archive.read_bytes())
        expected = {p.relative_to(regenerated) for p in regenerated.rglob("*") if p.is_file()}
        actual = {p.relative_to(source) for p in source.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts}
        assert actual == expected, (actual - expected, expected - actual)
        for relative in expected:
            assert (source / relative).read_bytes() == (regenerated / relative).read_bytes(), relative

        sys.path.insert(0, str(ROOT / "src"))
        threading.Thread.start = denied_thread
        from loguru import logger
        logger.remove()
        import nltk
        import websockets
        import pipecat.services.tts_service as tts_module
        import pipecat.transports.base_output as output_module
        import pipecat.processors.aggregators.llm_response_universal as aggregator_module
        from pipecat.audio.utils import SPEAKING_THRESHOLD, is_silence
        from pipecat.frames.frames import OutputDTMFFrame
        from pipecat.services.tts_service import TTSService
        from pipecat.transports.base_output import BaseOutputTransport
        from pipecat.transports.base_transport import TransportParams
        from pipecat.utils.text.english_punkt import DATA, sentence_tokenize

        assert importlib.metadata.version("nltk") == "3.9.2"
        assert importlib.metadata.version("websockets") == "15.0.1"
        for module in (nltk, websockets):
            assert not Path(module.__file__).is_relative_to(ROOT / "src"), module.__file__
        origins = {}
        for module in (tts_module, output_module, aggregator_module):
            path = Path(module.__file__)
            assert path.is_relative_to(source), path
            origins[module.__name__] = {
                "relative_path": path.relative_to(ROOT).as_posix(),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        forbidden_loaded = {"numpy", "PIL", "audioop", "loudness", "soxr", "onnxruntime"}.intersection(sys.modules)
        assert not forbidden_loaded, forbidden_loaded

        def denied_download(*args, **kwargs):
            raise AssertionError("Tokenizer attempted a runtime corpus download")
        nltk.download = denied_download
        data_root = temporary / "nltk_data"
        data_path = data_root / "tokenizers" / "punkt_tab" / "english"
        data_path.mkdir(parents=True)
        with zipfile.ZipFile(args.punkt_archive) as archive:
            for name, expected_hash in PUNKT_FILES.items():
                raw = archive.read(f"punkt_tab/english/{name}")
                assert DATA[name].encode("utf-8") == raw
                assert hashlib.sha256(raw).hexdigest() == expected_hash
                (data_path / name).write_bytes(raw)
        nltk.data.path[:] = [str(data_root)]
        from nltk.tokenize.punkt import PunktTokenizer
        reference = PunktTokenizer("english")
        texts = (
            "Tuesday morning is available. Thursday afternoon is also available.",
            "Dr. Smith arrives at 3.30 p.m. on Tuesday. Will that work?",
            'She said, "Yes, please." Then she left.',
            "Visit example.com for details. The cost is $4.25.",
            "Mr. Jones and Ms. Brown met in the U.S. They agreed.",
            "Wait... Is that right? Yes!",
            "Bonjour. Hello. こんにちは。你好！",
            "No punctuation yet",
            "First sentence.\n\nSecond sentence.",
            "",
        )
        comparisons = 0
        for text in texts:
            for length in range(len(text) + 1):
                prefix = text[:length]
                assert sentence_tokenize(prefix) == reference.tokenize(prefix), prefix
                comparisons += 1
        # Cover the two deliberate scalar edge semantics, plus the threshold.
        for samples, expected_silence in [([], True), ([0], True),
                ([SPEAKING_THRESHOLD, -SPEAKING_THRESHOLD], True),
                ([SPEAKING_THRESHOLD + 1], False), ([-32768], False), ([32767], False)]:
            audio = struct.pack("<" + "h" * len(samples), *samples)
            assert is_silence(audio) is expected_silence, samples

        class UnusedTTS(TTSService):
            async def run_tts(self, text, context_id):
                raise AssertionError("This constructor check must not call a TTS provider")
                yield  # Match the abstract async-generator interface.

        async def check_construction_and_dtmf():
            tts = UnusedTTS(sample_rate=24000)
            assert tts._resampler is None
            output = BaseOutputTransport(TransportParams(audio_out_enabled=True,
                                                         audio_out_sample_rate=24000))
            try:
                await output.write_dtmf(OutputDTMFFrame.from_string("1"))
            except NotImplementedError as error:
                assert str(error) == "DTMF audio is not included in the Workers voice configuration"
            else:
                raise AssertionError("DTMF must fail explicitly without omitted tone assets")
        asyncio.run(check_construction_and_dtmf())
        print(json.dumps({
            "passed": True,
            "scope": "CPython vendored speech provenance/import/tokenizer check; no Workers or provider execution",
            "python_files_reproduced": manifest["python_files"],
            "upstream_files": len(manifest["upstream_file_sha256"]),
            "patched_upstream_files": len(manifest["patches"]),
            "local_additions": len(manifest["local_additions"]),
            "english_tokenizer_prefix_comparisons": comparisons,
            "selected_origins": origins,
            "normal_dependencies": {"nltk": nltk.__version__, "websockets": websockets.__version__},
            "threads_created": 0,
            "forbidden_native_imports_loaded": sorted(forbidden_loaded),
            "b1_supported_package": False,
        }, indent=2))


if __name__ == "__main__":
    main()
