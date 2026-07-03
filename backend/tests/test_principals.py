"""Offline unit tests for the RBAC layer (app.principals) — the pure permission
matrix, the guarded-datasets enforcement primitive, and the provisioning plan driven
by a recording FakeClient (no cloud)."""

from __future__ import annotations

import pytest

from app import principals as P
from app.principals import AppRole, BrainKind, Permission


# --- permission matrix -------------------------------------------------------

def test_authorize_truth_table():
    # provider reads clinical; family does NOT
    assert P.authorize(AppRole.PROVIDER, BrainKind.CLINICAL, Permission.READ) is True
    assert P.authorize(AppRole.FAMILY, BrainKind.CLINICAL, Permission.READ) is False
    # owner may write/delete its own clinical brain; provider may not
    assert P.authorize(AppRole.OWNER, BrainKind.CLINICAL, Permission.WRITE) is True
    assert P.authorize(AppRole.OWNER, BrainKind.CLINICAL, Permission.DELETE) is True
    assert P.authorize(AppRole.PROVIDER, BrainKind.CLINICAL, Permission.WRITE) is False
    # everyone (owner/provider/family) may read the public reference brains
    for role in (AppRole.OWNER, AppRole.PROVIDER, AppRole.FAMILY):
        assert P.authorize(role, BrainKind.LITERATURE, Permission.READ) is True
        assert P.authorize(role, BrainKind.TRIALS, Permission.READ) is True


def test_authorize_fail_closed():
    assert P.authorize("stranger", BrainKind.CLINICAL) is False
    assert P.authorize(AppRole.FAMILY, "unknown_brain") is False
    assert P.denied(AppRole.FAMILY, BrainKind.CLINICAL) is True
    assert P.denied(AppRole.PROVIDER, BrainKind.CLINICAL) is False


def test_agent_brain_map_is_sane():
    # every agent reads the clinical brain; connections/trials/justify also read a ref brain
    for agent, brains in P.AGENT_BRAINS.items():
        assert BrainKind.CLINICAL in brains, agent
    assert BrainKind.LITERATURE in P.AGENT_BRAINS["connections"]
    assert BrainKind.TRIALS in P.AGENT_BRAINS["trials"]
    assert BrainKind.TRIALS in P.AGENT_BRAINS["justify"]


# --- guarded datasets (enforcement primitive) --------------------------------

@pytest.fixture
def fake_registry(monkeypatch):
    brains = {
        ("odyssey", "clinical"): {"name": "patient_odyssey_clinical__x", "id": "clin-id"},
        ("global", "literature"): {"name": "reference_literature__x", "id": "lit-id"},
        ("global", "trials"): {"name": "reference_trials__x", "id": "trials-id"},
    }

    def fake_get_active(patient_id, kind="clinical"):
        return brains.get((patient_id, kind))

    monkeypatch.setattr(P.registry, "get_active", fake_get_active)
    return brains


def test_guarded_datasets_denies_family_clinical(fake_registry):
    # the headline contract: family asking the clinical brain gets no datasets -> []
    assert P.guarded_datasets(AppRole.FAMILY, "odyssey", BrainKind.CLINICAL) == []


def test_guarded_datasets_allows_provider_clinical(fake_registry):
    assert P.guarded_datasets(AppRole.PROVIDER, "odyssey", BrainKind.CLINICAL) == \
        ["patient_odyssey_clinical__x"]


def test_guarded_datasets_reference_is_global(fake_registry):
    # family CAN read the public reference brains
    assert P.guarded_datasets(AppRole.FAMILY, "odyssey", BrainKind.LITERATURE) == \
        ["reference_literature__x"]


def test_guarded_datasets_missing_brain_returns_empty(fake_registry, monkeypatch):
    monkeypatch.setattr(P.registry, "get_active", lambda *a, **k: None)
    assert P.guarded_datasets(AppRole.PROVIDER, "odyssey", BrainKind.CLINICAL) == []


# --- provisioning plan (recording FakeClient, no cloud) ----------------------

class _FakeCloud:
    """Records RBAC calls and returns Cloud-shaped responses."""

    def __init__(self, existing_roles=None):
        self._roles = list(existing_roles or [])
        self.calls = []

    async def list_roles(self, tenant_id=None):
        self.calls.append(("list_roles", tenant_id))
        return list(self._roles)

    async def create_role(self, role_name):
        self.calls.append(("create_role", role_name))
        rid = f"role-{role_name}"
        self._roles.append({"id": rid, "name": role_name})
        return {"message": "Role created for tenant", "role_id": rid}

    async def grant_permission(self, principal_id, permission_name, dataset_ids):
        self.calls.append(("grant", principal_id, permission_name, tuple(dataset_ids)))
        return {"message": "Permission assigned to principal"}

    async def assign_user_role(self, user_id, role_id):
        self.calls.append(("assign", user_id, role_id))
        return {"message": "User added to role"}

    async def register_agent(self, agent_session_name, dataset_names=None, agent_type="api"):
        self.calls.append(("register", agent_session_name, tuple(dataset_names or [])))
        return {"id": f"{agent_session_name}-deadbeef", "datasets":
                [{"name": n, "role": "read_write"} for n in (dataset_names or [])]}


@pytest.fixture
def no_persist(monkeypatch):
    monkeypatch.setattr(P.registry, "set_meta", lambda *a, **k: None)


async def test_provision_grants_and_registers(fake_registry, no_persist):
    client = _FakeCloud()
    report = await P.provision(client, patient_id="odyssey",
                               tenant_id="t1", user_id="u1")

    # family role granted ONLY the reference brains (no clinical dataset id)
    fam = next(r for r in report.roles if r.role == AppRole.FAMILY)
    fam_ds = {g["dataset_id"] for g in fam.grants}
    assert "clin-id" not in fam_ds
    assert fam_ds == {"lit-id", "trials-id"}

    # provider role granted clinical + both reference brains
    prov = next(r for r in report.roles if r.role == AppRole.PROVIDER)
    assert {g["dataset_id"] for g in prov.grants} == {"clin-id", "lit-id", "trials-id"}

    # all six agents registered with a principal id
    assert {a.name for a in report.agents} == set(P.AGENT_BRAINS)
    assert report.all_agents_registered
    assert report.all_grants_ok


async def test_provision_is_idempotent_on_existing_role(fake_registry, no_persist):
    client = _FakeCloud(existing_roles=[{"id": "role-ariadne_family", "name": "ariadne_family"}])
    await P.provision(client, patient_id="odyssey", tenant_id="t1", user_id="u1")
    # family already existed -> no create_role for it
    created = [c[1] for c in client.calls if c[0] == "create_role"]
    assert "ariadne_family" not in created
    assert "ariadne_provider" in created


async def test_provision_reports_failed_grant(fake_registry, no_persist):
    client = _FakeCloud()

    async def boom(principal_id, permission_name, dataset_ids):
        raise RuntimeError("403")

    client.grant_permission = boom
    report = await P.provision(client, patient_id="odyssey", tenant_id="t1", user_id="u1")
    assert report.all_grants_ok is False
