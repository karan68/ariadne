"""Tiny JSON-backed registry mapping a logical patient brain to the *physical*
Cognee Cloud dataset currently backing it.

Why this exists: on the Cloud tenant, deleting a dataset is async and reusing the
same (deterministic) dataset name/id immediately after wedges it
(DATASET_PROCESSING_ERRORED). The reliable pattern is to always ingest into a
fresh, uniquely-named dataset. This registry records "for patient X, the clinical
brain is physically dataset <name>/<id>", so recall / RBAC / visualize resolve the
current dataset by a stable logical key even though the physical name is versioned.

Stored at backend/.state/registry.json (gitignored).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, Optional

_STATE_DIR = Path(__file__).resolve().parents[1] / ".state"
_REG_PATH = _STATE_DIR / "registry.json"
_lock = threading.Lock()


def _load() -> dict:
    if _REG_PATH.exists():
        try:
            return json.loads(_REG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save(data: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _REG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_active(patient_id: str, kind: str, name: str, dataset_id: Optional[str]) -> None:
    """Record the physical dataset (name + id) backing patient_id's `kind` brain."""
    with _lock:
        data = _load()
        data.setdefault(patient_id, {})[kind] = {"name": name, "id": dataset_id}
        _save(data)


def get_active(patient_id: str, kind: str = "clinical") -> Optional[Dict[str, Optional[str]]]:
    """Return {'name':..., 'id':...} for the patient's `kind` brain, or None."""
    return _load().get(patient_id, {}).get(kind)


def active_dataset_name(patient_id: str, kind: str = "clinical") -> Optional[str]:
    entry = get_active(patient_id, kind)
    return entry.get("name") if entry else None


def all_active() -> dict:
    return _load()


def set_meta(key: str, value: dict) -> None:
    """Store non-patient session metadata (e.g. the RBAC provisioning report) under
    a reserved top-level key. Keys are namespaced with a leading '_' so they never
    collide with a logical patient_id."""
    mkey = key if key.startswith("_") else f"_{key}"
    with _lock:
        data = _load()
        data[mkey] = value
        _save(data)


def get_meta(key: str) -> Optional[dict]:
    mkey = key if key.startswith("_") else f"_{key}"
    return _load().get(mkey)
