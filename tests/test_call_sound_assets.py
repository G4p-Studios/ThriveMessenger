import unittest
import wave
from pathlib import Path


CALL_SOUNDS = {
    "incoming_call.wav": (5.5, 6.1),
    "outgoing_call.wav": (3.5, 4.1),
    "call_connected.wav": (1.2, 1.6),
    "call_ended.wav": (1.2, 1.6),
    "group_call_join.wav": (1.2, 1.6),
    "group_call_leave.wav": (0.7, 1.1),
}


class CallSoundAssetTests(unittest.TestCase):
    def test_every_sound_pack_contains_short_pcm_call_cues(self):
        sounds_root = Path(__file__).resolve().parents[1] / "sounds"
        for pack in ("default", "galaxia", "skype"):
            for filename, duration_range in CALL_SOUNDS.items():
                with self.subTest(pack=pack, filename=filename):
                    path = sounds_root / pack / filename
                    self.assertTrue(path.is_file(), path)
                    with wave.open(str(path), "rb") as sound:
                        self.assertEqual(sound.getsampwidth(), 2)
                        self.assertEqual(sound.getframerate(), 44100)
                        self.assertEqual(sound.getnchannels(), 2)
                        duration = sound.getnframes() / sound.getframerate()
                    self.assertGreaterEqual(duration, duration_range[0])
                    self.assertLessEqual(duration, duration_range[1])


if __name__ == "__main__":
    unittest.main()
