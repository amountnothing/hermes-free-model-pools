"""Free Gemini API pool — companion plugin for free-models-sync.

Surfaces the Gemini API free tier (Gemini 3.5/3.8 Flash, Gemma 4) as its own picker
group via Google's OpenAI-compatible endpoint. EEA free-tier data is not used for
training, making this the cleanest free source. ``supports_model_listing=False`` keeps
paid Gemini models out of the group — the synced free list IS the catalog.
"""
import json
from pathlib import Path

from providers import register_provider
from providers.base import ProviderProfile

_STATE = Path.home() / ".hermes" / "free_models.json"
_SOURCE = "gemini-free-tier"


def _free_ids() -> tuple:
    try:
        data = json.loads(_STATE.read_text())
    except Exception:
        return ()
    return tuple(
        m["model"] for m in data.get("models", [])
        if m.get("source") == _SOURCE and m.get("model")
    )


class _FreeGeminiProfile(ProviderProfile):
    """fetch_models override so the switch validator accepts bare ids.

    Google's OpenAI-compat /models prefixes ids with ``models/`` while the curated free
    list uses bare ids. Without this override, ``models_validate._profile_catalog`` sees
    ``fetch_models`` as the base class's method and discards the provider's own catalog,
    then validates every switch against Google's prefixed live listing — which can never
    match a bare id (the prefix-strip there only handles ``normalized == "gemini"``).
    Overriding ``fetch_models`` (even trivially) marks the profile as owning its catalog,
    so the validator consults ``provider_model_ids()`` → the curated fallback instead.
    """

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0, **kwargs):
        if not self.supports_model_listing:
            return None
        return list(self.fallback_models or ())


register_provider(_FreeGeminiProfile(
    supports_model_listing=False,  # live catalog would list paid models too; curated fallback IS the catalog
    name="free-gemini",
    aliases=("free-google",),
    display_name="Free · Gemini API",
    description="Gemini API free tier (EEA: no training on free-tier data) — synced daily",
    signup_url="https://aistudio.google.com/apikey",
    env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    fallback_models=_free_ids(),
))
