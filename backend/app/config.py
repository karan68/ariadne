"""Runtime configuration for Ariadne.

Loads environment (reusing the hindsight-os provider pattern: Azure LLM + Ollama
embeddings) and exposes a single Settings object plus dataset-naming helpers.

The same code targets local open-source Cognee or Cognee Cloud: if COGNEE_BASE_URL
is set we run in "cloud" mode (cognee.serve), otherwise "local" mode.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # dotenv is optional at import time
    load_dotenv = None


def _load_env() -> None:
    """Load backend/.env with override=True (matches hindsight-os behaviour).

    Honours ARIADNE_SKIP_DOTENV=1 so tests can stay hermetic (no .env pollution).
    """
    if load_dotenv is None:
        return
    if os.getenv("ARIADNE_SKIP_DOTENV"):
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)


_load_env()


def _ensure_cognee_local_dirs() -> None:
    """Give Ariadne its own local Cognee stores so its graph state is isolated
    from other projects sharing this machine (e.g. hindsight-os / the cognee
    package default dir). Real env / .env still wins via setdefault."""
    backend = Path(__file__).resolve().parents[1]
    os.environ.setdefault("DATA_ROOT_DIRECTORY", str(backend / ".cognee_data"))
    os.environ.setdefault("SYSTEM_ROOT_DIRECTORY", str(backend / ".cognee_system"))


_ensure_cognee_local_dirs()


def _get(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- Cognee target (blank base url => local OSS Cognee) ---
    cognee_base_url: str = field(default_factory=lambda: _get("COGNEE_BASE_URL"))
    cognee_api_key: str = field(default_factory=lambda: _get("COGNEE_API_KEY"))
    cognee_tenant_id: str = field(default_factory=lambda: _get("COGNEE_TENANT_ID"))
    cognee_user_id: str = field(default_factory=lambda: _get("COGNEE_USER_ID"))

    # --- LLM (used by Cognee cognify for extraction) ---
    llm_provider: str = field(default_factory=lambda: _get("LLM_PROVIDER", "custom"))
    llm_model: str = field(default_factory=lambda: _get("LLM_MODEL", "azure/gpt-5.4"))
    llm_endpoint: str = field(default_factory=lambda: _get("LLM_ENDPOINT"))
    llm_api_key: str = field(default_factory=lambda: _get("LLM_API_KEY"))

    # --- Embeddings (local Ollama nomic-embed-text by default) ---
    embedding_provider: str = field(default_factory=lambda: _get("EMBEDDING_PROVIDER", "ollama"))
    embedding_model: str = field(default_factory=lambda: _get("EMBEDDING_MODEL", "nomic-embed-text"))
    embedding_dimensions: int = field(default_factory=lambda: int(_get("EMBEDDING_DIMENSIONS", "768")))

    # --- Ariadne app config ---
    reference_literature: str = field(default_factory=lambda: _get("ARIADNE_REF_LITERATURE", "reference_literature"))
    reference_trials: str = field(default_factory=lambda: _get("ARIADNE_REF_TRIALS", "reference_trials"))
    # Findings below this confidence are suppressed (alarm-fatigue control).
    min_confidence: float = field(default_factory=lambda: float(_get("ARIADNE_MIN_CONFIDENCE", "0.35")))

    @property
    def mode(self) -> str:
        return "cloud" if self.cognee_base_url else "local"

    def is_cloud(self) -> bool:
        return bool(self.cognee_base_url)

    # --- Per-patient dataset ("brain") naming ---
    @staticmethod
    def dataset_clinical(patient_id: str) -> str:
        return f"patient_{patient_id}_clinical"

    @staticmethod
    def dataset_general(patient_id: str) -> str:
        return f"patient_{patient_id}_general"


def get_settings() -> Settings:
    """Fresh Settings each call so tests can monkeypatch the environment."""
    return Settings()
