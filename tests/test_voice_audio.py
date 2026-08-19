import array
import unittest

from main import scale_pcm16


class VoiceAudioTests(unittest.TestCase):
    def test_pcm_gain_scales_samples(self):
        source = array.array("h", [1000, -1000, 20000]).tobytes()
        scaled = array.array("h")
        scaled.frombytes(scale_pcm16(source, 0.5))
        self.assertEqual(scaled.tolist(), [500, -500, 10000])

    def test_pcm_gain_clips_to_signed_16_bit(self):
        source = array.array("h", [30000, -30000]).tobytes()
        scaled = array.array("h")
        scaled.frombytes(scale_pcm16(source, 2.0))
        self.assertEqual(scaled.tolist(), [32767, -32768])


if __name__ == "__main__":
    unittest.main()
