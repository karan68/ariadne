"""Cognee Cloud REST client.

Talks to a hosted Cognee tenant over HTTP using the documented auth headers
(`X-Api-Key` + `X-Tenant-Id`). Implements the same core surface as the local
`CogneeClient` (remember/recall/search/forget) plus the Cloud-exclusive levers
Ariadne is built on: datasets, agents-as-principals, dataset permissions (RBAC),
sessions observability, graph visualization, quotas, and custom ontologies.

Endpoints verified against the tenant's live OpenAPI spec.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx

from .config import Settings, get_settings
from .cognee_client import BaseCogneeClient, QueryType

# cognify + graph_completion recalls invoke an LLM server-side and can be slow.
_LONG_TIMEOUT = httpx.Timeout(600.0, connect=30.0)
_SHORT_TIMEOUT = httpx.Timeout(60.0, connect=30.0)

#: the session-cache dataset the typed-entry endpoint defaults to (QA/feedback live
#: in the session cache, not the clinical graph). Verified live: qa+feedback round-trip.
FEEDBACK_DATASET = "main_dataset"


class CloudError(RuntimeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Cognee Cloud error {status}: {body[:500]}")
        self.status = status
        self.body = body


class CloudCogneeClient(BaseCogneeClient):
    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._client: Optional[httpx.AsyncClient] = None

    # --- lifecycle -----------------------------------------------------------
    async def connect(self) -> None:
        if not self.settings.cognee_base_url:
            raise CloudError(0, "COGNEE_BASE_URL not set; cannot use the Cloud client")
        headers = {"X-Api-Key": self.settings.cognee_api_key}
        if self.settings.cognee_tenant_id:
            headers["X-Tenant-Id"] = self.settings.cognee_tenant_id
        self._client = httpx.AsyncClient(
            base_url=self.settings.cognee_base_url.rstrip("/"),
            headers=headers,
            timeout=_SHORT_TIMEOUT,
        )

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            raise CloudError(0, "client not connected; call connect() first")
        return self._client

    @staticmethod
    def _ok(resp: httpx.Response) -> Any:
        if resp.status_code >= 400:
            raise CloudError(resp.status_code, resp.text)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            return resp.text

    # --- memory lifecycle ----------------------------------------------------
    async def remember(self, data, dataset_name, node_set=None, session_id=None,
                       graph_model=None, custom_prompt=None, self_improvement=None,
                       run_in_background=False):
        # `data` is a File field server-side: send text as text/plain file parts.
        items = data if isinstance(data, (list, tuple)) else [data]
        files = []
        for i, d in enumerate(items):
            raw = d.encode("utf-8") if isinstance(d, str) else d
            files.append(("data", (f"doc_{i}.txt", raw, "text/plain")))

        # httpx multipart requires `form` to be a Mapping; list values become
        # repeated form fields (used for node_set).
        form: Dict[str, Any] = {
            "datasetName": dataset_name,
            "run_in_background": "true" if run_in_background else "false",
        }
        if custom_prompt:
            form["custom_prompt"] = custom_prompt
        if graph_model is not None:
            form["graph_model"] = graph_model if isinstance(graph_model, str) else json.dumps(graph_model)
        if session_id:
            form["session_id"] = session_id
        if node_set:
            form["node_set"] = list(node_set)

        resp = await self._c().post("/api/v1/remember", data=form, files=files, timeout=_LONG_TIMEOUT)
        return self._ok(resp)

    async def recall(self, query_text, query_type=None, datasets=None, session_id=None,
                     include_references=True, only_context=False, top_k=None, node_name=None):
        body: Dict[str, Any] = {
            "query": query_text,
            "includeReferences": include_references,
            "onlyContext": only_context,
        }
        if query_type:
            body["searchType"] = query_type
        if datasets:
            body["datasets"] = datasets
        if session_id:
            body["sessionId"] = session_id
        if top_k is not None:
            body["topK"] = top_k
        if node_name:
            body["nodeName"] = node_name
        resp = await self._c().post("/api/v1/recall", json=body, timeout=_LONG_TIMEOUT)
        return self._ok(resp)

    async def search(self, query_text, search_type=QueryType.GRAPH_COMPLETION,
                     datasets=None, include_references=True, node_name=None, top_k=None):
        body: Dict[str, Any] = {
            "query": query_text,
            "searchType": search_type,
            "includeReferences": include_references,
        }
        if datasets:
            body["datasets"] = datasets
        if node_name:
            body["nodeName"] = node_name
        if top_k is not None:
            body["topK"] = top_k
        resp = await self._c().post("/api/v1/search", json=body, timeout=_LONG_TIMEOUT)
        return self._ok(resp)

    async def forget(self, data_id=None, dataset=None, everything=False, memory_only=False):
        body: Dict[str, Any] = {"everything": everything, "memoryOnly": memory_only}
        if data_id:
            body["dataId"] = data_id
        if dataset:
            body["dataset"] = dataset
        resp = await self._c().post("/api/v1/forget", json=body, timeout=_LONG_TIMEOUT)
        return self._ok(resp)

    async def improve(self, dataset, session_ids=None, feedback_alpha=None,
                      build_global_context_index=False, *, feedback=None, session_id=None):
        """Cloud `improve()`/memify is **feedback-driven**: there is no standalone
        memify endpoint (verified against the live 49-path OpenAPI spec). The improve
        lifecycle is realized by attaching feedback to the QAs a recall produced —
        `POST /remember/entry {type:"feedback", qa_id, feedback_score}` — which the
        Cloud dispatches to `SessionManager.add_feedback` and stages under each QA's
        `memify_metadata.feedback_weights_applied`.

        Pass `feedback=[(qa_id, score, text), ...]` (+ the `session_id` those QAs live
        under) to submit a batch. Returns `{applied, results:[remember_entry json,...]}`.
        """
        if not feedback:
            raise NotImplementedError(
                "Cloud improve is feedback-driven — pass feedback=[(qa_id, score, text), ...] "
                "and session_id, or use app.feedback.submit_feedback()/improve_findings().")
        results = []
        for qa_id, score, text in feedback:
            results.append(await self.remember_entry(
                {"type": "feedback", "qa_id": qa_id,
                 "feedback_score": score, "feedback_text": text or ""},
                dataset_name=dataset or FEEDBACK_DATASET, session_id=session_id))
        return {"status": "ok", "applied": len(results), "results": results}

    async def remember_entry(self, entry: Dict[str, Any], dataset_name: str = FEEDBACK_DATASET,
                             session_id: Optional[str] = None, retries: int = 6,
                             backoff: float = 2.0) -> Any:
        """POST a typed entry (`qa`|`feedback`|`trace`|`skill_run`) to the session cache.

        The remember pipeline returns a transient **409 Conflict** ("An error occurred
        during remember.") under lock contention; this retries with linear backoff
        (verified live: the same qa+feedback round-trip 409s under load and succeeds
        once the tenant settles).
        """
        body: Dict[str, Any] = {"entry": entry, "dataset_name": dataset_name}
        if session_id is not None:
            body["session_id"] = session_id
        last_exc: Optional[CloudError] = None
        for attempt in range(retries):
            resp = await self._c().post("/api/v1/remember/entry", json=body, timeout=_SHORT_TIMEOUT)
            if resp.status_code < 400:
                return self._ok(resp)
            if resp.status_code == 409:
                last_exc = CloudError(resp.status_code, resp.text)
                await asyncio.sleep(backoff * (attempt + 1))
                continue
            raise CloudError(resp.status_code, resp.text)
        raise last_exc or CloudError(409, "remember_entry exhausted retries")

    # --- Cloud-exclusive: datasets ------------------------------------------
    async def list_datasets(self) -> Any:
        return self._ok(await self._c().get("/api/v1/datasets/"))

    async def datasets_status(self, dataset_ids: Optional[List[str]] = None) -> Any:
        params = {"dataset_ids": dataset_ids} if dataset_ids else None
        return self._ok(await self._c().get("/api/v1/datasets/status", params=params))

    async def create_dataset(self, name: str) -> Any:
        return self._ok(await self._c().post("/api/v1/datasets/", json={"name": name}))

    async def delete_dataset(self, dataset_id: str) -> Any:
        return self._ok(await self._c().delete(f"/api/v1/datasets/{dataset_id}"))

    async def dataset_graph(self, dataset_id: str) -> Any:
        return self._ok(await self._c().get(f"/api/v1/datasets/{dataset_id}/graph"))

    # --- Cloud-exclusive: agents as principals ------------------------------
    async def register_agent(self, agent_session_name: str, dataset_names: Optional[List[str]] = None,
                             session_id: Optional[str] = None, metadata: Optional[dict] = None,
                             agent_type: str = "api") -> Any:
        body: Dict[str, Any] = {"agent_session_name": agent_session_name, "type": agent_type}
        if dataset_names:
            body["dataset_names"] = dataset_names
        if session_id:
            body["session_id"] = session_id
        if metadata:
            body["metadata"] = metadata
        return self._ok(await self._c().post("/api/v1/agents/register", json=body))

    async def agent_connections(self, agent_id: Optional[str] = None, range: str = "7d",
                                active_only: bool = False, limit: int = 50) -> Any:
        params: Dict[str, Any] = {"range": range, "active_only": active_only, "limit": limit}
        if agent_id:
            params["agent_id"] = agent_id
        return self._ok(await self._c().get("/api/v1/agents/connections", params=params))

    async def unregister_agent(self, agent_session_name: str) -> Any:
        return self._ok(await self._c().post(
            "/api/v1/agents/unregister", json={"agent_session_name": agent_session_name}))

    # --- Cloud-exclusive: dataset permissions (RBAC) ------------------------
    async def grant_permission(self, principal_id: str, permission_name: str,
                               dataset_ids: List[str]) -> Any:
        # principal_id must be a UUID (a user_id or role_id); permission_name is a
        # query param; the body is a bare JSON list of dataset ids. (Verified live.)
        resp = await self._c().post(
            f"/api/v1/permissions/datasets/{principal_id}",
            params={"permission_name": permission_name},
            json=dataset_ids,
        )
        return self._ok(resp)

    async def create_role(self, role_name: str) -> Any:
        # role_name is a query param (not body). Returns {message, role_id, tenant_id}.
        return self._ok(await self._c().post(
            "/api/v1/permissions/roles", params={"role_name": role_name}))

    def _tenant(self, tenant_id: Optional[str]) -> str:
        tid = tenant_id or self.settings.cognee_tenant_id
        if not tid:
            raise CloudError(0, "tenant_id required (set COGNEE_TENANT_ID)")
        return tid

    async def tenant_me(self) -> Any:
        return self._ok(await self._c().get("/api/v1/permissions/tenants/me"))

    async def list_roles(self, tenant_id: Optional[str] = None) -> Any:
        return self._ok(await self._c().get(
            f"/api/v1/permissions/tenants/{self._tenant(tenant_id)}/roles"))

    async def list_users(self, tenant_id: Optional[str] = None) -> Any:
        return self._ok(await self._c().get(
            f"/api/v1/permissions/tenants/{self._tenant(tenant_id)}/users"))

    async def role_users(self, role_id: str, tenant_id: Optional[str] = None) -> Any:
        return self._ok(await self._c().get(
            f"/api/v1/permissions/tenants/{self._tenant(tenant_id)}/roles/{role_id}/users"))

    async def assign_user_role(self, user_id: str, role_id: str) -> Any:
        # role_id is a query param. Returns {message: "User added to role"}.
        return self._ok(await self._c().post(
            f"/api/v1/permissions/users/{user_id}/roles", params={"role_id": role_id}))

    # --- Cloud-exclusive: sessions observability ----------------------------
    async def sessions(self, range: str = "7d", limit: int = 50) -> Any:
        return self._ok(await self._c().get("/api/v1/sessions", params={"range": range, "limit": limit}))

    async def session_stats(self, range: str = "7d") -> Any:
        return self._ok(await self._c().get("/api/v1/sessions/stats", params={"range": range}))

    async def sessions_cost_by_model(self, range: str = "7d") -> Any:
        return self._ok(await self._c().get("/api/v1/sessions/cost-by-model", params={"range": range}))

    async def session_detail(self, session_id: str) -> Any:
        """Single-session audit trail: the session row plus its `qas` (question/answer) log."""
        return self._ok(await self._c().get(f"/api/v1/sessions/{session_id}"))

    # --- Cloud-exclusive: visualization, quotas, ontologies -----------------
    async def visualize(self, dataset_id: str) -> Any:
        return self._ok(await self._c().get("/api/v1/visualize", params={"dataset_id": dataset_id}))

    async def quota_usage(self) -> Any:
        return self._ok(await self._c().get("/api/v1/quotas/usage"))

    async def list_ontologies(self) -> Any:
        return self._ok(await self._c().get("/api/v1/ontologies"))

    async def health(self) -> Any:
        return self._ok(await self._c().get("/health"))
