"""Ariadne RBAC — agents-as-principals + dataset permission grants.

Grounded in the live Cognee Cloud tenant (verified against the OpenAPI spec and by
real calls):

  * Auth is a single `X-Api-Key` mapping to one owner user; there is no API to mint a
    second human credential. The Cloud therefore always authorizes recall as the owner
    (who owns every dataset).
  * The Cloud *does* provide the full RBAC substrate, all verified live returning 200:
      - roles as UUID principals            POST /permissions/roles?role_name=…
      - dataset grants to a UUID principal  POST /permissions/datasets/{uuid}?permission_name=read
      - user↔role assignment                POST /permissions/users/{uid}/roles?role_id=…
      - agent principals (attribution)      POST /permissions/agents/register
    `principal_id` on a grant must be a UUID → the grantee is a user or a role, NOT an
    agent-connection id (agents are the observability/attribution plane).

So Ariadne's design (honest about the single-key constraint):

  * **Cloud = the RBAC substrate & audit source of truth.** We materialize roles
    (provider, family) and real dataset grants that mirror the matrix below, and we
    register every agent as its own principal for Sessions attribution.
  * **Ariadne's backend = the enforcement boundary.** It authenticates the app
    principal (patient / clinician / family), maps them to a role, and consults the
    matrix *before* routing any recall. A denied (principal, brain) pair yields **no
    datasets → an empty `[]` recall** and never touches the Cloud. In a real
    multi-tenant deployment each human would hold their own Cognee key and the Cloud
    would enforce the same grants directly.

This module is import-safe and cloud-free for its pure surface (the matrix, `authorize`,
`guarded_datasets`); only `provision()` and friends talk to the Cloud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app import registry
from app.config import get_settings


# --- taxonomy ----------------------------------------------------------------

class BrainKind:
    """The brains that actually exist in the live tenant."""
    CLINICAL = "clinical"      # the patient's private longitudinal record
    LITERATURE = "literature"  # global curated reference brain
    TRIALS = "trials"          # global curated trials brain


REFERENCE_BRAINS = (BrainKind.LITERATURE, BrainKind.TRIALS)
ALL_BRAINS = (BrainKind.CLINICAL, BrainKind.LITERATURE, BrainKind.TRIALS)


class Permission:
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


class AppRole:
    OWNER = "owner"        # the patient — owns their clinical brain
    PROVIDER = "provider"  # a treating clinician, granted read on the clinical brain
    FAMILY = "family"      # a caregiver — public reference only, NO clinical access


#: role → brain → permissions the role holds on that brain.
ROLE_MATRIX: Dict[str, Dict[str, frozenset]] = {
    AppRole.OWNER: {
        BrainKind.CLINICAL: frozenset({Permission.READ, Permission.WRITE, Permission.DELETE}),
        BrainKind.LITERATURE: frozenset({Permission.READ}),
        BrainKind.TRIALS: frozenset({Permission.READ}),
    },
    AppRole.PROVIDER: {
        BrainKind.CLINICAL: frozenset({Permission.READ}),
        BrainKind.LITERATURE: frozenset({Permission.READ}),
        BrainKind.TRIALS: frozenset({Permission.READ}),
    },
    AppRole.FAMILY: {
        # deliberately NO clinical entry → clinical recall is denied → []
        BrainKind.LITERATURE: frozenset({Permission.READ}),
        BrainKind.TRIALS: frozenset({Permission.READ}),
    },
}

#: which brains each swarm agent reads (drives its principal's dataset connections).
AGENT_BRAINS: Dict[str, List[str]] = {
    "timeline": [BrainKind.CLINICAL],
    "connections": [BrainKind.CLINICAL, BrainKind.LITERATURE],
    "trials": [BrainKind.CLINICAL, BrainKind.TRIALS],
    "briefing": [BrainKind.CLINICAL],
    "safety": [BrainKind.CLINICAL],
    "justify": [BrainKind.CLINICAL, BrainKind.TRIALS],
}

#: canonical (stable) Cloud role names Ariadne provisions. Chosen once because roles
#: cannot be deleted via the API — provisioning is idempotent by this name.
ROLE_NAMES = {AppRole.PROVIDER: "ariadne_provider", AppRole.FAMILY: "ariadne_family"}


# --- pure enforcement --------------------------------------------------------

def authorize(role: str, brain: str, permission: str = Permission.READ) -> bool:
    """True iff `role` holds `permission` on `brain` per the matrix. Unknown roles or
    brains are denied by default (fail-closed)."""
    return permission in ROLE_MATRIX.get(role, {}).get(brain, frozenset())


def denied(role: str, brain: str, permission: str = Permission.READ) -> bool:
    return not authorize(role, brain, permission)


def _brain_patient(patient_id: str, brain: str) -> str:
    """Reference brains are global; the clinical brain is per-patient."""
    return "global" if brain in REFERENCE_BRAINS else patient_id


def resolve_dataset(patient_id: str, brain: str) -> Optional[dict]:
    """Registry entry {name,id} for a (patient, brain), or None if not seeded."""
    return registry.get_active(_brain_patient(patient_id, brain), brain)


def guarded_datasets(role: str, patient_id: str, brain: str,
                     permission: str = Permission.READ) -> List[str]:
    """The dataset-name list a recall should target for (role, patient, brain).

    Returns the resolved dataset name **only if the role is authorized**; a denied
    pair returns `[]`, so the caller's recall surfaces nothing — the enforcement
    point for the family→clinical→[] contract. Missing (unseeded) brains also
    return [] rather than raising, so the app degrades gracefully.
    """
    if not authorize(role, brain, permission):
        return []
    entry = resolve_dataset(patient_id, brain)
    name = entry.get("name") if entry else None
    return [name] if name else []


# --- provisioning report -----------------------------------------------------

@dataclass
class RoleGrant:
    role: str
    role_name: str
    role_id: Optional[str] = None
    grants: List[dict] = field(default_factory=list)   # {brain, dataset_id, permission, ok}


@dataclass
class AgentPrincipal:
    name: str
    principal_id: Optional[str] = None
    brains: List[str] = field(default_factory=list)
    dataset_names: List[str] = field(default_factory=list)


@dataclass
class ProvisionReport:
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    roles: List[RoleGrant] = field(default_factory=list)
    agents: List[AgentPrincipal] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "roles": [
                {"role": r.role, "role_name": r.role_name, "role_id": r.role_id,
                 "grants": r.grants}
                for r in self.roles
            ],
            "agents": [
                {"name": a.name, "principal_id": a.principal_id,
                 "brains": a.brains, "dataset_names": a.dataset_names}
                for a in self.agents
            ],
        }

    @property
    def all_grants_ok(self) -> bool:
        return bool(self.roles) and all(
            g["ok"] for r in self.roles for g in r.grants)

    @property
    def all_agents_registered(self) -> bool:
        return bool(self.agents) and all(a.principal_id for a in self.agents)


# --- provisioning helpers ----------------------------------------------------

def _matrix_dataset_ids(patient_id: str, role: str) -> List[tuple]:
    """[(brain, permission, dataset_id)] for every brain the role can read that is
    actually seeded. Only READ grants are provisioned to the Cloud (write/delete stay
    the owner's)."""
    out = []
    for brain, perms in ROLE_MATRIX.get(role, {}).items():
        if Permission.READ not in perms:
            continue
        entry = resolve_dataset(patient_id, brain)
        did = entry.get("id") if entry else None
        if did:
            out.append((brain, Permission.READ, did))
    return out


async def _ensure_role(client, role_name: str, tenant_id: Optional[str]) -> Optional[str]:
    """Return the role_id for `role_name`, creating it if absent (idempotent)."""
    existing = await client.list_roles(tenant_id)
    for r in existing or []:
        if r.get("name") == role_name:
            return r.get("id")
    created = await client.create_role(role_name)
    if isinstance(created, dict) and created.get("role_id"):
        return created["role_id"]
    # fall back to a re-list if the create response shape differs
    for r in (await client.list_roles(tenant_id)) or []:
        if r.get("name") == role_name:
            return r.get("id")
    return None


async def provision(client, patient_id: str = "odyssey",
                    tenant_id: Optional[str] = None,
                    user_id: Optional[str] = None,
                    assign_owner_to_provider: bool = False) -> ProvisionReport:
    """Materialize the RBAC matrix on the live Cloud tenant, idempotently.

    Creates the provider + family roles, grants each role READ on the brains the
    matrix allows (family gets only the global reference brains → no clinical), and
    registers every swarm agent as its own principal connected to the brains it reads.
    Persists the report to the registry (`_rbac`). Requires a connected cloud client.
    """
    settings = get_settings()
    tenant_id = tenant_id or settings.cognee_tenant_id or None
    user_id = user_id or settings.cognee_user_id or None
    report = ProvisionReport(tenant_id=tenant_id, user_id=user_id)

    # roles + dataset grants
    for role in (AppRole.PROVIDER, AppRole.FAMILY):
        role_name = ROLE_NAMES[role]
        rid = await _ensure_role(client, role_name, tenant_id)
        rg = RoleGrant(role=role, role_name=role_name, role_id=rid)
        if rid:
            for brain, perm, did in _matrix_dataset_ids(patient_id, role):
                ok = True
                try:
                    await client.grant_permission(rid, perm, [did])
                except Exception:
                    ok = False
                rg.grants.append({"brain": brain, "dataset_id": did,
                                  "permission": perm, "ok": ok})
            if assign_owner_to_provider and role == AppRole.PROVIDER and user_id:
                try:
                    await client.assign_user_role(user_id, rid)
                except Exception:
                    pass
        report.roles.append(rg)

    # agent principals (attribution) connected to the brains each reads
    for agent_name, brains in AGENT_BRAINS.items():
        names = []
        for b in brains:
            entry = resolve_dataset(patient_id, b)
            if entry and entry.get("name"):
                names.append(entry["name"])
        ap = AgentPrincipal(name=agent_name, brains=list(brains), dataset_names=names)
        try:
            resp = await client.register_agent(
                agent_session_name=f"ariadne_{agent_name}", dataset_names=names, agent_type="api")
            if isinstance(resp, dict):
                ap.principal_id = resp.get("id")
        except Exception:
            ap.principal_id = None
        report.agents.append(ap)

    registry.set_meta("rbac", report.to_dict())
    return report
