from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OMNIVOICE_", env_file=".env")

    host: str = "0.0.0.0"
    port: int = 9194
    workers: int = 4
    log_level: str = "info"
    log_dir: str = "logs"
    log_run_id: str = ""
    log_file: str = ""
    pid_file: str = ""
    log_retention_days: int = Field(default=7, ge=0)

    model_id: str = "k2-fsa/OmniVoice"
    runner_mode: str = Field(default="hybrid", pattern="^(official|triton|hybrid)$")
    dtype: str = Field(default="fp16", pattern="^(fp16|bf16|fp32)$")
    device: str = "cuda"

    infer_host: str = "127.0.0.1"
    infer_port: int = 0
    inferers: str = ""
    inferer_name: str = ""
    inferer_kind: str = "gpu"
    infer_start_timeout_s: float = 180.0
    request_timeout_s: float = 300.0
    metrics_shm_path: str = ""
    metrics_shm_size: int = Field(default=1_048_576, ge=4096)
    metrics_snapshot_interval_s: float = Field(default=0.5, ge=0.05)

    gpu_inferer: int = Field(default=1, ge=0)

    batch_size: int = Field(default=16, ge=1)
    batch_wait_ms: int = Field(default=250, ge=1)
    cuda_streams: int = Field(default=2, ge=1)
    cuda_graph_min_width: int = Field(default=32, ge=1)
    cuda_graph_max_width: int = Field(default=0, ge=0)
    cuda_graph_auto_width_tokens_per_word: int = Field(default=16, ge=1)
    cuda_graph_auto_max_width: int = Field(default=2048, ge=128)
    max_continuity_audio_tokens: int = Field(default=64, ge=0)
    max_continuity_text_words: int = Field(default=24, ge=0)
    max_clone_audio_prompt_cache: int = Field(default=32, ge=0)

    sample_rate: int = 24000
    max_sse_audio_b64_chars: int = 48_000
    clone_prompt_shared_cache_dir: str = ""

    # Internal generation defaults. These are intentionally not request-tunable.
    num_step: int = 32
    guidance_scale: float = 2.0
    denoise: bool = True
    t_shift: float = 0.1
    position_temperature: float = 5.0
    class_temperature: float = 0.0
    layer_penalty_factor: float = 5.0
    audio_chunk_duration: float = 15.0
    audio_chunk_threshold: float = 30.0
    postprocess_output: bool = True

    text_chunk_words: int = Field(default=32, ge=4)
    text_chunk_soft_overflow_ratio: float = Field(default=0.12, ge=0.0)
    text_chunk_same_sentence_penalty: int = Field(default=1, ge=0)
    text_chunk_sentence_boundary_penalty: int = Field(default=4, ge=0)
    text_chunk_fragment_boundary_penalty: int = Field(default=24, ge=0)
    text_chunk_short_underfill_ratio: float = Field(default=0.5, ge=0.0)
    text_chunk_short_underfill_penalty: int = Field(default=1, ge=0)
    default_voice_instructions: str = "warm, clear, natural speaking voice"
