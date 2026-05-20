from __future__ import annotations

import io
import wave

import numpy as np


def float_to_pcm16(audio: np.ndarray) -> bytes:
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr = np.clip(arr, -1.0, 1.0)
    return (arr * 32767.0).astype("<i2").tobytes()


def pcm16_to_wav(pcm: bytes, sample_rate: int = 24000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return out.getvalue()


def pcm_chunks_to_format(chunks: list[bytes], response_format: str, sample_rate: int) -> bytes:
    pcm = b"".join(chunks)
    fmt = response_format.lower()
    if fmt == "pcm":
        return pcm
    if fmt in {"wav", "mp3", "opus", "aac", "flac"}:
        if fmt != "wav":
            # Keep the first implementation dependency-light and deterministic.
            # OpenAI-compatible clients can request wav/pcm now; compressed
            # formats should be added with ffmpeg validation later.
            raise ValueError(f"response_format={fmt!r} is not implemented; use 'wav' or 'pcm'")
        return pcm16_to_wav(pcm, sample_rate)
    raise ValueError(f"Unsupported response_format={response_format!r}")


def media_type_for_format(response_format: str) -> str:
    fmt = response_format.lower()
    if fmt == "pcm":
        return "audio/pcm"
    if fmt == "wav":
        return "audio/wav"
    return f"audio/{fmt}"
