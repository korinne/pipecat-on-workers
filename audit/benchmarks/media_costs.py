#!/usr/bin/env python3
"""Measure this repo's PCM resamplers on LOCAL CPython, never infer Workers CPU."""
import argparse
import hashlib
import importlib.util
import json
import math
import platform
import statistics
import struct
import time
from pathlib import Path


def pcm_bytes_per_second(rate, channels, bits=16):
    if rate <= 0 or channels <= 0 or bits <= 0 or bits % 8:
        raise ValueError("Positive sample rate/channels and whole-byte samples required")
    return rate * channels * (bits // 8)


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def run(repo, seconds):
    source = repo / "src/sfu_codec.py"
    spec = importlib.util.spec_from_file_location("audited_sfu_codec", source)
    codec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(codec)
    # Prepare a deterministic 20 ms sine frame outside the timed region.
    incoming = b"".join(struct.pack("<hh", v, v) for v in
                        (int(6000 * math.sin(2 * math.pi * 1000 * n / 48000)) for n in range(960)))
    outgoing = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 1000 * n / 24000)))
                        for n in range(480))
    incoming_packet = codec.encode_packet(incoming)
    input_converter, output_converter = codec.InputResampler(), codec.OutputResampler()
    cpu, wall = [], []
    for _ in range(round(seconds * 50)):
        c0, w0 = time.process_time_ns(), time.perf_counter_ns()
        decoded = codec.decode_packet(incoming_packet)[2]
        downsampled = input_converter.convert(decoded)
        upsampled = output_converter.convert(outgoing)
        packet = codec.encode_packet(upsampled)
        cpu.append((time.process_time_ns() - c0) / 1e6)
        wall.append((time.perf_counter_ns() - w0) / 1e6)
        if len(downsampled) != 640 or len(upsampled) != 3840 or not packet:
            raise AssertionError("Unexpected 20 ms output shape")
    audio_seconds = len(cpu) * .02
    rates = {"provider_input_16khz_mono": pcm_bytes_per_second(16000, 1),
             "provider_output_24khz_mono": pcm_bytes_per_second(24000, 1),
             "sfu_48khz_stereo_each_direction": pcm_bytes_per_second(48000, 2)}
    return {
        "scope": "Local CPython PCM resampling and packet wrapping, synthetic full-duplex audio",
        "status": "measured_locally_only", "workers_performance_status": "untested",
        "moq_codec_performance_status": "untested", "opus_codec_exercised": False,
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "audio_seconds_each_direction": audio_seconds, "frame_ms": 20,
        "local_process_cpu_ms_per_audio_second_duplex": round(sum(cpu) / audio_seconds, 4),
        "local_frame_cpu_ms": {"median": statistics.median(cpu), "p95": percentile(cpu, .95), "max": max(cpu)},
        "local_frame_wall_ms": {"median": statistics.median(wall), "p95": percentile(wall, .95), "max": max(wall)},
        "derived_payload_bytes_per_second": rates,
        "derived_provider_duplex_10min_unbounded_payload_bytes": (rates["provider_input_16khz_mono"] + rates["provider_output_24khz_mono"]) * 600,
        "derived_sfu_duplex_10min_unbounded_payload_bytes": 2 * rates["sfu_48khz_stereo_each_direction"] * 600,
        "caveats": [
            "The 10 minute figures are hypothetical unbounded queues, not observed allocations.",
            "No Workers CPU, whole-isolate memory, scheduling, FFI, QUIC, Opus, provider, or acoustic quality measured.",
            "These local timings must not be used to estimate Worker capacity or compare architectures.",
            "No readiness threshold has been agreed; this command cannot report production acceptance."
        ]
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=10)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 120:
        parser.error("--seconds must be 1 to 120")
    result = run(args.repo.resolve(), args.seconds)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: result[k] for k in ["status", "workers_performance_status", "opus_codec_exercised", "local_process_cpu_ms_per_audio_second_duplex"]}))
