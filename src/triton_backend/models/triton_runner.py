"""Triton-optimized OmniVoice runner."""

import logging

from triton_backend.models.base_runner import BaseRunner
from triton_backend.models.patching import (
    apply_sage_attention,
    apply_triton_kernels,
    find_patchable_model,
)

logger = logging.getLogger(__name__)


class TritonRunner(BaseRunner):
    """BaseRunner with Triton kernel patching applied after model load.

    Replaces RMSNorm, SwiGLU, Norm+Residual ops with fused Triton kernels
    across the Qwen3-0.6B LLM backbone layers. Optionally replaces
    attention with SageAttention.

    Args:
        patch_range: Half-open ``(start, end)`` range of decoder layer
            indices to patch. ``None`` patches all layers.
        enable_sage_attention: Replace SDPA with SageAttention. Requires
            ``pip install sageattention``. Gracefully skips if unavailable.
        device: Target device (default: "cuda").
        model_id: HuggingFace model ID.
        dtype: Model dtype string (``"bf16"``, ``"fp16"``, ``"fp32"``).
    """

    def __init__(
        self,
        patch_range: tuple[int, int] | None = None,
        enable_sage_attention: bool = False,
        device: str = "cuda",
        model_id: str = "k2-fsa/OmniVoice",
        dtype: str = "fp16",
        attn_backend: str = "auto",
    ) -> None:
        super().__init__(
            device=device,
            model_id=model_id,
            dtype=dtype,
            attn_backend=attn_backend,
        )
        self.patch_range = patch_range
        self.require_sage_attention = attn_backend == "sageattention"
        self.enable_sage_attention = enable_sage_attention or self.require_sage_attention

    def load_model(self) -> None:
        """Load model then apply Triton kernel patches."""
        super().load_model()
        patchable = find_patchable_model(self._model)
        apply_triton_kernels(
            patchable,
            patch_range=self.patch_range,
        )
        if self.enable_sage_attention:
            patched = apply_sage_attention(patchable, patch_range=self.patch_range)
            if self.require_sage_attention and patched <= 0:
                raise RuntimeError(
                    "attn_backend=sageattention was requested, but no attention modules were patched. "
                    "Install the sage extra and verify sageattention supports this GPU."
                )
        logger.info("TritonRunner ready (Triton kernels applied).")
