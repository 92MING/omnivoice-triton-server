from __future__ import annotations

OPENAI_VOICE_PRESETS = {
    "alloy": "middle-aged, moderate pitch",
    "ash": "male, young adult, low pitch",
    "ballad": "female, young adult, moderate pitch",
    "coral": "female, young adult, high pitch",
    "echo": "male, middle-aged, moderate pitch",
    "fable": "male, young adult, british accent",
    "nova": "female, young adult, moderate pitch",
    "onyx": "male, middle-aged, very low pitch",
    "sage": "female, middle-aged, moderate pitch",
    "shimmer": "female, young adult, very high pitch",
}


def resolve_mode_and_instruct(voice: str | None, instructions: str | None, default: str) -> tuple[str, str | None]:
    if instructions:
        return "design", instructions
    key = (voice or "auto").strip().lower()
    if key in {"", "auto"}:
        return "auto", None
    if key.startswith("design:"):
        return "design", key.split(":", 1)[1].strip()
    if key in OPENAI_VOICE_PRESETS:
        return "design", OPENAI_VOICE_PRESETS[key]
    # Treat unknown OpenAI voice strings as design instructions rather than
    # silently dropping them.
    return "design", voice or default
