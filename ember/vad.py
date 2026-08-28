"""Lightweight streaming Silero VAD using ONNX Runtime and NumPy only."""
from __future__ import annotations

from pathlib import Path

import numpy as np


class SileroVoiceActivityDetector:
    FRAME_SAMPLES = 512
    CONTEXT_SAMPLES = 64

    def __init__(self, model_path: str | Path, sample_rate: int = 16000):
        if int(sample_rate) != 16000:
            raise ValueError("Ember's Silero VAD currently requires 16000 Hz audio.")
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(Path(model_path)), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.sample_rate = int(sample_rate)
        self.reset()

    def reset(self) -> None:
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, self.CONTEXT_SAMPLES), dtype=np.float32)

    def probability(self, audio) -> float:
        frame = np.asarray(audio, dtype=np.float32).reshape(-1)
        if len(frame) != self.FRAME_SAMPLES:
            raise ValueError(f"Silero VAD needs {self.FRAME_SAMPLES} samples per frame.")
        model_input = np.concatenate((self.context, frame.reshape(1, -1)), axis=1)
        output, self.state = self.session.run(None, {
            "input": model_input,
            "state": self.state,
            "sr": np.asarray(self.sample_rate, dtype=np.int64),
        })
        self.context = model_input[:, -self.CONTEXT_SAMPLES:]
        return float(np.asarray(output).reshape(-1)[0])

    def is_speech(self, audio, threshold: float = 0.5) -> tuple[bool, float]:
        probability = self.probability(audio)
        return probability >= float(threshold), probability

