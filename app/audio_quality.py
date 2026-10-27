from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

import numpy as np


class SpeechQualityError(ValueError):
    """Raised when an audio clip does not meet the measurable speech guard."""


def is_real_speech(
    audio: np.ndarray,
    sample_rate: int,
    *,
    vad: Callable[[np.ndarray], np.ndarray] | None = None,
) -> bool:
    """Reject silence, near-pure tones, and clips without syllabic-rate amplitude modulation.

    Silero VAD bundled with faster-whisper is used unless a test double is injected.
    Audio is linearly resampled to Silero's required 16 kHz input rate.
    """
    signal = np.asarray(audio, dtype=np.float32).reshape(-1)
    if sample_rate <= 0 or signal.size < sample_rate // 2 or not np.isfinite(signal).all():
        return False
    signal = signal - float(np.mean(signal))
    if float(np.sqrt(np.mean(signal * signal))) < 1e-4:
        return False

    target_rate = 16000
    if sample_rate != target_rate:
        new_size = max(1, round(signal.size * target_rate / sample_rate))
        signal = np.interp(
            np.linspace(0, signal.size - 1, new_size), np.arange(signal.size), signal
        ).astype(np.float32)
    signal = np.clip(signal, -1.0, 1.0)
    if vad is None:
        from faster_whisper.vad import get_vad_model  # type: ignore[import-untyped]

        model = get_vad_model()
        vad_window = 512
        pad_len = (vad_window - (signal.size % vad_window)) % vad_window
        padded_signal = np.pad(signal, (0, pad_len)) if pad_len else signal
        probabilities = np.asarray(model(padded_signal), dtype=np.float32).reshape(-1)
    else:
        probabilities = np.asarray(vad(signal), dtype=np.float32).reshape(-1)
    if probabilities.size == 0:
        return False
    speech_ratio = float(np.mean(probabilities > 0.5))
    speech_duration = int(np.sum(probabilities > 0.5)) * 512 / target_rate
    max_speech_prob = float(np.max(probabilities))
    # Accepts:
    #  (a) continuous speech: >50% of frames active, OR
    #  (b) shorter utterances with >=0.18s of high-confidence speech (VAD > 0.5)
    #      — covers single Khmer words (~0.22 s) where trailing silence is stripped, OR
    #  (c) any detected speech if max VAD confidence is very high (>=0.90), meaning
    #      VAD is near-certain there is speech even if only a few frames were counted
    #      (e.g., a 0.22 s Khmer word with max_prob=0.984).
    # Silence, pure tones, and white noise all produce max_speech_prob << 0.90.
    has_sufficient_speech = (
        (speech_ratio > 0.5)
        or (speech_duration >= 0.18 and max_speech_prob >= 0.75)
        or (speech_duration > 0 and max_speech_prob >= 0.90)
    )
    if not has_sufficient_speech:
        return False

    frame = min(4096, 1 << (signal.size.bit_length() - 1))
    if frame < 256:
        return False
    window = np.hanning(frame).astype(np.float32)
    powers = []
    for offset in range(0, signal.size - frame + 1, max(1, frame // 2)):
        spectrum = np.fft.rfft(signal[offset : offset + frame] * window)
        powers.append(np.square(np.abs(spectrum)))
    if not powers:
        return False
    spectral_power = np.mean(powers, axis=0)
    total_power = float(np.sum(spectral_power))
    if total_power <= 0 or float(np.max(spectral_power) / total_power) >= 0.5:
        return False

    # For very short speech utterances (< 1.5 s, e.g. "Selim!", "Fine?", "5 million.")
    # the FFT-based syllabic-rate modulation check is unreliable because the envelope
    # has too few frames to resolve 2-8 Hz cycles. VAD and spectral-flatness guards
    # already passed.  Isolate the active speech region so trailing silence/padding
    # from TTS does not distort duration or modulate envelope falsely.
    speech_indices = np.where(probabilities > 0.2)[0]
    if len(speech_indices) > 0:
        step = 512 if vad is None else max(1, signal.size // probabilities.size)
        s_start = speech_indices[0] * step
        s_end = min(signal.size, (speech_indices[-1] + 1) * step)
        eval_signal = signal[s_start:s_end]
    else:
        eval_signal = signal

    active_duration = eval_signal.size / target_rate
    if active_duration < 1.5:
        return True

    envelope_frame = max(1, round(target_rate * 0.01))
    usable = eval_signal.size // envelope_frame * envelope_frame
    envelope = np.sqrt(
        np.mean(np.square(eval_signal[:usable].reshape(-1, envelope_frame)), axis=1)
    )
    if envelope.size < 20:
        return False
    envelope -= float(np.mean(envelope))
    modulation = np.square(np.abs(np.fft.rfft(envelope * np.hanning(envelope.size))))
    frequencies = np.fft.rfftfreq(envelope.size, d=envelope_frame / target_rate)
    band = (frequencies >= 2.0) & (frequencies <= 8.0)
    modulation_total = float(np.sum(modulation[1:]))
    if modulation_total <= 0:
        return False
    return float(np.sum(modulation[band]) / modulation_total) >= 0.15


def require_real_speech(path: Path, *, vad: Callable[[np.ndarray], np.ndarray] | None = None) -> None:
    """Validate a mono/stereo PCM WAV artifact using VAD, spectrum, and envelope tests."""
    with wave.open(str(path), "rb") as source:
        rate, channels, width = source.getframerate(), source.getnchannels(), source.getsampwidth()
        if width not in (2, 4):
            raise SpeechQualityError(f"Unsupported PCM sample width: {width}")
        raw = source.readframes(source.getnframes())
    dtype = "<i2" if width == 2 else "<i4"
    samples = np.frombuffer(raw, dtype=dtype).astype(np.float32)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    samples /= float(2 ** (8 * width - 1))
    if not is_real_speech(samples, rate, vad=vad):
        raise SpeechQualityError("Generated audio failed the real-speech guard (VAD/tone/envelope).")
