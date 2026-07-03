"""Thin wrapper around the Cognee SDK.

`cognee` is imported lazily inside methods so the rest of the app (and the unit
tests) import without requiring Cognee to be installed. Two implementations share
one interface:

  * CogneeClient      - real SDK calls (local OSS or Cloud via serve()).
  * MockCogneeClient  - deterministic canned responses for tests + demo-mode.

The real client is exercised live starting in P1; P0 tests use the mock.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .config import Settings, get_settings


# Recall/search strategy names (map to Cognee SearchType in the real client).
class QueryType:
    GRAPH_COMPLETION = "GRAPH_COMPLETION"
    TEMPORAL = "TEMPORAL"
    SUMMARIES = "SUMMARIES"
    CHUNKS = "CHUNKS"


class BaseCogneeClient:
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def remember(
        self,
        data: Any,
        dataset_name: str,
        node_set: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        graph_model: Any = None,
        custom_prompt: Optional[str] = None,
        self_improvement: Optional[bool] = None,
    ) -> Any: ...

    async def recall(
        self,
        query_text: str,
        query_type: Optional[str] = None,
        datasets: Optional[List[str]] = None,
        session_id: Optional[str] = None,
        include_references: bool = True,
        only_context: bool = False,
        top_k: Optional[int] = None,
        node_name: Optional[List[str]] = None,
    ) -> Any: ...

    async def search(
        self,
        query_text: str,
        search_type: str = QueryType.GRAPH_COMPLETION,
        datasets: Optional[List[str]] = None,
        include_references: bool = True,
    ) -> Any: ...

    async def improve(
        self,
        dataset: str,
        session_ids: Optional[List[str]] = None,
        feedback_alpha: Optional[float] = None,
        build_global_context_index: bool = False,
    ) -> Any: ...

    async def forget(self, data_id: Optional[str] = None, dataset: Optional[str] = None,
                     everything: bool = False) -> Any: ...


class CogneeClient(BaseCogneeClient):
    """Real Cognee SDK client. Same code targets local OSS or Cognee Cloud."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._connected = False

    async def connect(self) -> None:
        import cognee

        if self.settings.is_cloud():
            await cognee.serve(url=self.settings.cognee_base_url, api_key=self.settings.cognee_api_key)
        self._connected = True

    async def disconnect(self) -> None:
        import cognee

        if self.settings.is_cloud() and hasattr(cognee, "disconnect"):
            await cognee.disconnect()
        self._connected = False

    def _search_type(self, name: Optional[str]):
        if not name:
            return None
        try:
            from cognee import SearchType  # top-level export (validated via hindsight)

            return getattr(SearchType, name)
        except Exception:
            return name  # fall back to the string; refined against the live SDK in P1

    async def remember(self, data, dataset_name, node_set=None, session_id=None,
                       graph_model=None, custom_prompt=None, self_improvement=None):
        import cognee

        kwargs = {"dataset_name": dataset_name}
        if node_set is not None:
            kwargs["node_set"] = node_set
        if session_id is not None:
            kwargs["session_id"] = session_id
        if graph_model is not None:
            kwargs["graph_model"] = graph_model
        if custom_prompt is not None:
            kwargs["custom_prompt"] = custom_prompt
        if self_improvement is not None:
            kwargs["self_improvement"] = self_improvement
        return await cognee.remember(data, **kwargs)

    async def recall(self, query_text, query_type=None, datasets=None, session_id=None,
                     include_references=True, only_context=False, top_k=None, node_name=None):
        import cognee

        kwargs: dict = {"query_text": query_text, "include_references": include_references,
                        "only_context": only_context}
        qt = self._search_type(query_type)
        if qt is not None:
            kwargs["query_type"] = qt
        if datasets is not None:
            kwargs["datasets"] = datasets
        if session_id is not None:
            kwargs["session_id"] = session_id
        if top_k is not None:
            kwargs["top_k"] = top_k
        if node_name is not None:
            kwargs["node_name"] = node_name
        return await cognee.recall(**kwargs)

    async def search(self, query_text, search_type=QueryType.GRAPH_COMPLETION,
                     datasets=None, include_references=True):
        import cognee

        kwargs: dict = {"query_text": query_text, "include_references": include_references}
        st = self._search_type(search_type)
        if st is not None:
            kwargs["query_type"] = st
        if datasets is not None:
            kwargs["datasets"] = datasets
        return await cognee.search(**kwargs)

    async def improve(self, dataset, session_ids=None, feedback_alpha=None,
                      build_global_context_index=False):
        import cognee

        kwargs: dict = {"dataset": dataset, "build_global_context_index": build_global_context_index}
        if session_ids is not None:
            kwargs["session_ids"] = session_ids
        if feedback_alpha is not None:
            kwargs["feedback_alpha"] = feedback_alpha
        return await cognee.improve(**kwargs)

    async def forget(self, data_id=None, dataset=None, everything=False):
        import cognee

        kwargs: dict = {}
        if data_id is not None:
            kwargs["data_id"] = data_id
        if dataset is not None:
            kwargs["dataset"] = dataset
        if everything:
            kwargs["everything"] = True
        return await cognee.forget(**kwargs)


class MockCogneeClient(BaseCogneeClient):
    """Deterministic client for tests and demo-mode (no network / no LLM)."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.calls: List[tuple] = []

    async def connect(self) -> None:
        self.calls.append(("connect",))

    async def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    async def remember(self, data, dataset_name, node_set=None, session_id=None,
                       graph_model=None, custom_prompt=None, self_improvement=None):
        self.calls.append(("remember", dataset_name))
        return {"status": "ok", "dataset": dataset_name, "items_processed": 1}

    async def recall(self, query_text, query_type=None, datasets=None, session_id=None,
                     include_references=True, only_context=False, top_k=None, node_name=None):
        self.calls.append(("recall", query_text, query_type))
        return []

    async def search(self, query_text, search_type=QueryType.GRAPH_COMPLETION,
                     datasets=None, include_references=True):
        self.calls.append(("search", query_text, search_type))
        return []

    async def improve(self, dataset, session_ids=None, feedback_alpha=None,
                      build_global_context_index=False):
        self.calls.append(("improve", dataset))
        return {"status": "ok"}

    async def forget(self, data_id=None, dataset=None, everything=False):
        self.calls.append(("forget", data_id, dataset))
        return {"status": "ok", "forgotten": data_id}


def get_client(mock: bool = False, settings: Optional[Settings] = None) -> BaseCogneeClient:
    settings = settings or get_settings()
    if mock:
        return MockCogneeClient(settings)
    if settings.is_cloud():
        from .cloud_client import CloudCogneeClient  # lazy: avoids import cycle

        return CloudCogneeClient(settings)
    return CogneeClient(settings)
