from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

FORBIDDEN_REQUEST_PARAMS = {
    "audio_chunk_duration",
    "audio_chunk_threshold",
    "batch_mode",
    "position_temperature",
    "num_step",
    "postprocess_output",
}

KNOWN_SPEECH_FIELDS = {
    "model",
    "input",
    "voice",
    "speaker",
    "instructions",
    "response_format",
    "speed",
    "stream",
    "duration",
    "language",
    "chunk_mode",
    "extra_fields",
    "request_timeout_s",
}


class SpeechRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str = "tts-1"
    input: str = Field(..., min_length=1)
    voice: str = "auto"
    speaker: str | None = None
    instructions: str | None = None
    response_format: str = "wav"
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    stream: bool = False
    duration: float | None = Field(default=None, ge=0.05, le=120.0)
    language: str | None = None
    chunk_mode: Literal["concurrent", "sequential", "none"] = "concurrent"
    request_timeout_s: float | None = Field(default=None, ge=1.0, le=1200.0)
    extra_fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def pack_unknown_kwargs(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        forbidden = sorted(k for k in data if k in FORBIDDEN_REQUEST_PARAMS)
        if forbidden:
            raise ValueError(
                "Request parameters are server-controlled and may not be supplied: "
                + ", ".join(forbidden)
            )
        extra = dict(data.get("extra_fields") or {})
        cleaned = {}
        for key, value in data.items():
            if key in KNOWN_SPEECH_FIELDS:
                cleaned[key] = value
            else:
                extra[key] = value
        cleaned["extra_fields"] = extra
        return cleaned


class InferRequest(BaseModel):
    request_id: str
    input: str
    chunks: list[str] = Field(default_factory=list)
    chunk_durations: list[float | None] = Field(default_factory=list)
    mode: str
    instruct: str | None = None
    response_format: str = "wav"
    speed: float = 1.0
    duration: float | None = None
    language: str | None = None
    chunk_mode: Literal["concurrent", "sequential", "none"] = "concurrent"
    ref_text: str | None = None
    ref_audio: str | None = None
    ref_audio_b64: str | None = None
    stream: bool = False
    extra_fields: dict[str, Any] = Field(default_factory=dict)


class InferTask(BaseModel):
    request_id: str
    task_id: str
    seq: int
    text: str
    chunks: list[str] = Field(default_factory=list)
    chunk_durations: list[float | None] = Field(default_factory=list)
    mode: str
    instruct: str | None = None
    speed: float = 1.0
    duration: float | None = None
    language: str | None = None
    chunk_mode: Literal["concurrent", "sequential", "none"] = "concurrent"
    ref_text: str | None = None
    ref_audio: str | None = None
    ref_audio_b64: str | None = None
    extra_fields: dict[str, Any] = Field(default_factory=dict)
