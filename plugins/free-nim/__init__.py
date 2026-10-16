"""Free NVIDIA NIM pool — companion plugin for free-models-sync.

Picker group shows ONLY the synced free ids: ``supports_model_listing=False`` keeps the
full live NIM catalog (106 models, mostly paid) out of the picker, while calls still run
against the real NIM endpoint with ``NVIDIA_API_KEY``. The id list is re-read from
``~/.hermes/free_models.json`` on every process start, so the daily 02:10 sync updates
this group without touching this file.
"""
import json
from pathlib import Path

from providers import register_provider
from providers.base import ProviderProfile

_STATE = Path.home() / ".hermes" / "free_models.json"
_SOURCE = "nvidia-free-endpoint"


_BAD_STATUSES = {"DEAD", "LOCKED", "NOKEY"}


def _free_ids() -> tuple:
    try:
        data = json.loads(_STATE.read_text())
    except Exception:
        return ()
    return tuple(
        m["model"] for m in data.get("models", [])
        if m.get("source") in (_SOURCE, "nvidia-free-endpoint-auto")
        and m.get("model")
        and m.get("status", "OK") not in _BAD_STATUSES
    )


register_provider(ProviderProfile(
    supports_model_listing=False,  # live catalog would list paid models too; curated fallback IS the catalog
    name="free-nim",
    aliases=("free-nvidia",),
    display_name="Free · NVIDIA NIM",
    description="NVIDIA NIM free endpoints (40 RPM) — synced daily by free-models-sync",
    signup_url="https://build.nvidia.com",
    env_vars=("NVIDIA_API_KEY",),
    base_url="https://integrate.api.nvidia.com/v1",
    fallback_models=_free_ids(),
))
