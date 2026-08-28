import unittest
from unittest.mock import patch

import numpy as np

from ember.vad import SileroVoiceActivityDetector


class FakeSession:
    def __init__(self, *args, **kwargs):
        self.inputs = []

    def run(self, _outputs, inputs):
        self.inputs.append(inputs)
        return np.asarray([[0.73]], dtype=np.float32), inputs["state"] + 1


class SileroVADTests(unittest.TestCase):
    def test_streaming_state_and_context_are_preserved(self):
        with patch("onnxruntime.InferenceSession", FakeSession):
            vad = SileroVoiceActivityDetector("unused.onnx")
            voiced, probability = vad.is_speech(np.ones(512, dtype=np.float32), 0.5)

        self.assertTrue(voiced)
        self.assertAlmostEqual(probability, 0.73, places=2)
        self.assertEqual(vad.context.shape, (1, 64))
        self.assertTrue(np.all(vad.state == 1))

    def test_wrong_frame_size_is_rejected(self):
        with patch("onnxruntime.InferenceSession", FakeSession):
            vad = SileroVoiceActivityDetector("unused.onnx")
        with self.assertRaises(ValueError):
            vad.probability(np.zeros(100, dtype=np.float32))


if __name__ == "__main__":
    unittest.main()
