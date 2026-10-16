"""Free OpenCode Go pool — companion plugin for free-models-sync.

Surfaces the Go subscription's free-unlimited tier (LongCat 2.5 Preview Free, Step 5
Preview Free, Cat Jam Free) as its own picker group. Reuses ``OPENCODE_GO_API_KEY`` and
the Go relay base_url, so inference is identical to the built-in ``opencode-go`` provider;
``supports_model_listing=False`` keeps the live relay catalog (which rotates free ids in
and out) from polluting the group — the synced free list IS the catalog.
"""
import json
from pathlib import Path

from hermes_cli.version_info import get_version_info
from providers import register_provider
from providers.base import ProviderProfile

_STATE = Path.home() / ".hermes" / "free_models.json"
_SOURCE = "opencode-go-free"

# Same attribution headers as the built-in opencode-go profile.
_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
    "User-Agent": f"HermesAgent/{get_version_info().base_version}",
}


_BAD_STATUSES = {"DEAD", "LOCKED", "NOKEY"}


def _free_ids() -> tuple:
    try:
        data = json.loads(_STATE.read_text())
    except Exception:
        return ()
    return tuple(
        m["model"] for m in data.get("models", [])
        if m.get("source") == _SOURCE and m.get("model")
        and m.get("status", "OK") not in _BAD_STATUSES
    )


register_provider(ProviderProfile(
    supports_model_listing=False,  # live catalog would list paid models too; curated fallback IS the catalog
    name="free-go",
    aliases=("free-opencode-go",),
    display_name="Free · OpenCode Go",
    description="OpenCode Go free-unlimited tier — synced daily by free-models-sync",
    signup_url="https://opencode.ai/go",
    env_vars=("OPENCODE_GO_API_KEY",),
    base_url="https://opencode.ai/zen/go/v1",
    default_headers=dict(_ATTRIBUTION_HEADERS),
    fallback_models=_free_ids(),
))
