import math
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from sfu_codec import InputResampler, OutputResampler, decode_packet, encode_packet


class SfuCodecTests(unittest.TestCase):
    def test_packet_with_metadata_and_unknown_fields(self):
        audio = struct.pack("<hhhh", 1200, -1200, 32767, -32768)
        packet = b"\x08\x81\x01\x10\x05\x32\x03abc" + encode_packet(audio)
        self.assertEqual(decode_packet(packet), (129, 5, audio))
        self.assertEqual(decode_packet(encode_packet(b"")), (None, None, b""))

    def test_malformed_and_oversized_packets_are_rejected(self):
        for packet in (b"\x08", b"\x2a\xff\x01x", b"\x00", b"\x2a\x01x",
                       b"\x08\x01\x08\x02", b"\x09" + bytes(8),
                       b"\x08\xff\xff\xff\xff\x10", bytes(32769)):
            with self.subTest(packet=packet[:12]):
                with self.assertRaises(ValueError):
                    decode_packet(packet)
        with self.assertRaises(ValueError):
            encode_packet(bytes(32768))
        self.assertLessEqual(len(encode_packet(bytes(32760))), 32768)

    def test_input_streaming_matches_whole_and_rejects_aliasing(self):
        def tone(hz):
            return b"".join(struct.pack("<hh", *(2 * [round(12000 * math.sin(2 * math.pi * hz * n / 48000))]))
                            for n in range(4800))

        pcm = tone(1000)
        whole = InputResampler().convert(pcm)
        stream = InputResampler()
        pieces = b"".join(stream.convert(pcm[n:n + 28]) for n in range(0, len(pcm), 28))
        self.assertEqual(pieces, whole)
        self.assertEqual(len(whole), 3200)

        def energy(audio):
            samples = [v[0] for v in struct.iter_unpack("<h", audio)][100:]
            return sum(value * value for value in samples) / len(samples)

        alias = InputResampler().convert(tone(16000))
        self.assertLess(energy(alias), energy(whole) / 1000)

    def test_stereo_downmix_and_output_streaming(self):
        self.assertEqual(InputResampler().convert(struct.pack("<hh", 2000, -2000) * 60), bytes(40))
        pcm = struct.pack("<hhhh", -30000, -1000, 1000, 30000)
        result = OutputResampler().convert(pcm)
        stream = OutputResampler()
        self.assertEqual(result, stream.convert(pcm[:2]) + stream.convert(pcm[2:6]) + stream.convert(pcm[6:]))
        self.assertEqual(len(result), len(pcm) * 4)
        for left, right in struct.iter_unpack("<hh", result):
            self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
