"""Free OpenCode Console pool — companion plugin for free-models-sync.

Surfaces the OpenCode Console free tier (MiMo V2.6 Flash Free — AA 37.9, ~200 req/day,
limited-time) as its own picker group via the OpenCode inference endpoint. Uses its own
``OPENCODE_API_KEY`` (the console key, distinct from the Go subscription key).
``supports_model_listing=False`` keeps paid console models out — the synced free list IS
the catalog.
"""
import json
from pathlib import Path

from providers import register_provider
from providers.base import ProviderProfile

_STATE = Path.home() / ".hermes" / "free_models.json"
_SOURCE = "opencode-console-free"


def _free_ids() -> tuple:
    try:
        data = json.loads(_STATE.read_text())
    except Exception:
        return ()
    return tuple(
        m["model"] for m in data.get("models", [])
        if m.get("source") == _SOURCE and m.get("model")
    )


register_provider(ProviderProfile(
    supports_model_listing=False,  # live catalog would list paid models too; curated fallback IS the catalog
    name="free-console",
    aliases=("free-opencode-console",),
    display_name="Free · OpenCode Console",
    description="OpenCode Console free tier (MiMo V2.6 Flash Free) — synced daily by free-models-sync",
    signup_url="https://opencode.ai/console",
    env_vars=("OPENCODE_API_KEY",),
    base_url="https://opencode.ai/inference/v1",
    fallback_models=_free_ids(),
))
