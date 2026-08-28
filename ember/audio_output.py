"""Safe preparation of generated mono speech for a hardware output stream."""
from __future__ import annotations

from math import gcd
import numpy as np
from scipy.signal import resample_poly


def prepare_playback_audio(audio, source_rate, output_rate, peak_limit=0.92):
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not len(samples):
        return samples
    samples = np.nan_to_num(samples, nan=0.0, posinf=0.0, neginf=0.0)
    samples = samples - np.mean(samples, dtype=np.float64)
    source_rate = int(source_rate)
    output_rate = int(output_rate)
    if source_rate <= 0 or output_rate <= 0:
        raise ValueError("Audio sample rates must be positive.")
    if source_rate != output_rate:
        divisor = gcd(source_rate, output_rate)
        samples = resample_poly(
            samples, output_rate // divisor, source_rate // divisor
        ).astype(np.float32)
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    limit = max(0.1, min(0.99, float(peak_limit)))
    if peak > limit:
        samples = samples * (limit / peak)
    return np.ascontiguousarray(samples, dtype=np.float32)
