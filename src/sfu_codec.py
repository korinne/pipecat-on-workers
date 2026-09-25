"""Small, dependency-free codecs for the Realtime SFU WebSocket adapter.

The adapter carries protobuf Packet messages, not naked PCM. Its PCM is signed
16-bit little-endian, 48 kHz interleaved stereo. The application continues using
16 kHz mono input and 24 kHz mono output. All resampler state spans messages.
"""

import math
import struct
from collections import deque

MAX_PACKET_BYTES = 32768


def _varint(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def encode_packet(payload):
    payload = bytes(payload)
    if len(payload) % 4:
        raise ValueError("SFU PCM must contain complete stereo samples")
    packet = b"\x2a" + _varint(len(payload)) + payload
    if len(packet) > MAX_PACKET_BYTES:
        raise ValueError("SFU packet exceeds the 32 KB message limit")
    return packet


def decode_packet(message):
    """Return (sequence, timestamp, payload), safely skipping unknown fields."""
    message = bytes(message)
    if len(message) > MAX_PACKET_BYTES:
        raise ValueError("SFU packet exceeds the 32 KB message limit")
    offset = 0

    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            if offset >= len(message):
                raise ValueError("Truncated SFU protobuf varint")
            byte = message[offset]
            offset += 1
            if shift == 63 and byte > 1:
                raise ValueError("Oversized SFU protobuf varint")
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Oversized SFU protobuf varint")

    values = {}
    while offset < len(message):
        tag = varint()
        field, wire = tag >> 3, tag & 7
        if not field:
            raise ValueError("Invalid SFU protobuf field")
        if field in (1, 2, 5):
            if field in values:
                raise ValueError("Duplicate SFU protobuf field")
            if wire != (2 if field == 5 else 0):
                raise ValueError("Invalid SFU protobuf wire type")
        if wire == 0:
            value = varint()
            if field in (1, 2) and value > 0xffffffff:
                raise ValueError("SFU sequence or timestamp exceeds uint32")
        elif wire in (1, 2, 5):
            size = varint() if wire == 2 else (8 if wire == 1 else 4)
            if offset + size > len(message):
                raise ValueError("Truncated SFU protobuf field")
            value = message[offset:offset + size]
            offset += size
        else:
            raise ValueError("Unsupported SFU protobuf wire type")
        if field in (1, 2, 5):
            values[field] = value
    payload = values.get(5, b"")
    if len(payload) % 4:
        raise ValueError("SFU PCM must contain complete stereo samples")
    return values.get(1), values.get(2), payload


def _pcm(value):
    return max(-32768, min(32767, round(value)))


class InputResampler:
    """48 kHz stereo → 16 kHz mono, with a small anti-alias FIR filter.

    A 63-tap Hamming-windowed sinc attenuates frequencies above the 16 kHz
    output's Nyquist limit. Its fixed delay is less than one millisecond.
    """

    def __init__(self):
        size, cutoff = 63, 7000 / 48000
        taps = []
        for index in range(size):
            x = index - (size - 1) / 2
            sinc = 2 * cutoff if x == 0 else math.sin(2 * math.pi * cutoff * x) / (math.pi * x)
            taps.append(sinc * (0.54 - 0.46 * math.cos(2 * math.pi * index / (size - 1))))
        total = sum(taps)
        self.taps = tuple(value / total for value in taps)
        self.history = deque([0.0] * size, maxlen=size)
        self.phase = 0

    def convert(self, pcm):
        pcm = bytes(pcm)
        if len(pcm) % 4:
            raise ValueError("Incomplete SFU stereo sample")
        result = bytearray()
        for left, right in struct.iter_unpack("<hh", pcm):
            self.history.appendleft((left + right) / 2)
            self.phase += 1
            if self.phase == 3:
                self.phase = 0
                sample = _pcm(sum(a * b for a, b in zip(self.taps, self.history)))
                result.extend(struct.pack("<h", sample))
        return bytes(result)


class OutputResampler:
    """24 kHz mono → 48 kHz stereo using streaming linear interpolation."""

    def __init__(self):
        self.previous = None

    def convert(self, pcm):
        pcm = bytes(pcm)
        if len(pcm) % 2:
            raise ValueError("Incomplete TTS mono sample")
        result = bytearray()
        for (sample,) in struct.iter_unpack("<h", pcm):
            previous = sample if self.previous is None else self.previous
            midpoint = _pcm((previous + sample) / 2)
            result.extend(struct.pack("<hhhh", midpoint, midpoint, sample, sample))
            self.previous = sample
        return bytes(result)
