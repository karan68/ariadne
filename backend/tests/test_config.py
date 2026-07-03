import importlib

import app.config as config_mod


def _fresh_settings(monkeypatch, **env):
    # Keep config tests hermetic: never let a real backend/.env override the
    # values under test (the runtime still loads .env normally in production).
    monkeypatch.setenv("ARIADNE_SKIP_DOTENV", "1")
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    importlib.reload(config_mod)
    return config_mod.get_settings()


def test_local_mode_when_no_base_url(monkeypatch):
    s = _fresh_settings(monkeypatch, COGNEE_BASE_URL=None)
    assert s.mode == "local"
    assert s.is_cloud() is False


def test_cloud_mode_when_base_url_set(monkeypatch):
    s = _fresh_settings(monkeypatch, COGNEE_BASE_URL="https://api.cognee.ai", COGNEE_API_KEY="k")
    assert s.mode == "cloud"
    assert s.is_cloud() is True
    assert s.cognee_api_key == "k"


def test_dataset_naming_is_deterministic_and_isolated():
    s = config_mod.get_settings()
    assert s.dataset_clinical("odyssey") == "patient_odyssey_clinical"
    assert s.dataset_general("odyssey") == "patient_odyssey_general"
    assert s.dataset_clinical("a") != s.dataset_clinical("b")


def test_embedding_defaults():
    s = config_mod.get_settings()
    assert s.embedding_model  # non-empty
    assert s.embedding_dimensions == 768
