# [DNA_TAG]
# ORIGIN: Crostini-Chromebook
# PILLAR: armory
# DEPS: store
# ROLE: Anchor model selection + performance record
# AUTHOR: Bleak
# SESSION: 2026-09-15
# TIER: 3
# [/DNA_TAG]

"""models — which model Anchor asks, and what it learned from last time.

DEFAULT_MODEL is a *preset name*, not a hardcoded model id. Hardcoding
"openai/gpt-oss-120b" was actively harmful here: that is a Groq model, and all
Groq keys are revoked, so every candidate provider got sent a model id it does
not serve. The name "default" resolves through ~/.config/ai/config.toml and
lets the gateway pick a valid model per provider.
"""

from __future__ import annotations

import time

DEFAULT_MODEL = "default"


def pick_model(prompt: str) -> tuple[str, str]:
    """Return (model, task_type). Task detection stays simple on purpose."""
    text = (prompt or "").lower()
    if any(w in text for w in ("traceback", "error", "exception", "bug", "fix")):
        return DEFAULT_MODEL, "debug"
    if any(w in text for w in ("search", "find", "look up", "what is", "who is")):
        return DEFAULT_MODEL, "research"
    return DEFAULT_MODEL, "general"


def refresh_models() -> bool:
    """Kept for CLI compatibility. The gateway owns the model list now."""
    try:
        import ai_lib
        return bool(ai_lib.list_models())
    except Exception:
        return False


def record_perf(model: str, task: str, success: bool, latency_ms: int) -> None:
    """Persist one call so latency/success history is not thrown away."""
    try:
        import store
        store.log_tool(f"model:{model}", f"task={task} latency_ms={latency_ms}",
                       success, "")
    except Exception:
        pass
