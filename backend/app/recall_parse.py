"""Parse Cognee Cloud recall/search responses into a structured, citation-aware form.

The Cloud returns a list of result objects; the synthesized answer is in `text`,
and with `includeReferences=True` the citations are inlined at the end of `text`
as an `Evidence:` block, e.g.:

    <answer markdown>

    Evidence:
    - chunk 1 of document doc_0 (data_id: <uuid>, chunk_id: <uuid>): "quoted source…"
    - chunk 2 of document doc_3 (data_id: <uuid>, chunk_id: <uuid>): "another quote…"

`structured` is currently null, so we parse that block into EvidenceRef objects.
This is the single choke point every agent uses to obtain traceable citations
(and to enforce the citation-required invariant).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List, Optional

from .models import EvidenceRef

# Splits the answer body from the trailing citations block.
_EVIDENCE_SPLIT_RE = re.compile(r"\n\s*Evidence\s*:\s*\n", re.IGNORECASE)

# Per-citation extractors (applied to each bullet entry; tolerant of field order).
_DATA_ID_RE = re.compile(r"data_id:\s*([0-9a-fA-F][0-9a-fA-F\-]{7,})")
_CHUNK_ID_RE = re.compile(r"chunk_id:\s*([0-9a-fA-F][0-9a-fA-F\-]{7,})")
_DOC_RE = re.compile(r"document\s+([^\s(]+)")
_SNIPPET_RE = re.compile(r"\"(.*?)\"", re.DOTALL)


@dataclass
class RecallResult:
    answer: str = ""
    references: List[EvidenceRef] = field(default_factory=list)
    kind: Optional[str] = None
    search_type: Optional[str] = None
    source: Optional[str] = None
    dataset_ids: List[str] = field(default_factory=list)
    raw: Any = None

    @property
    def has_citations(self) -> bool:
        return any(r.data_id or r.chunk_id for r in self.references)


def _iter_items(response: Any) -> List[dict]:
    if response is None:
        return []
    if isinstance(response, dict):
        # Some endpoints may wrap results; be permissive.
        for key in ("results", "data", "items"):
            if isinstance(response.get(key), list):
                return [x for x in response[key] if isinstance(x, dict)]
        return [response]
    if isinstance(response, list):
        return [x for x in response if isinstance(x, dict)]
    return []


def _parse_evidence_block(block: str) -> List[EvidenceRef]:
    refs: List[EvidenceRef] = []
    # Entries are bullet lines; split on newline-dash while keeping multiline quotes.
    entries = re.split(r"\n\s*[-*]\s+", "\n" + block.strip())
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        data_id = _DATA_ID_RE.search(entry)
        chunk_id = _CHUNK_ID_RE.search(entry)
        doc = _DOC_RE.search(entry)
        snippet = _SNIPPET_RE.search(entry)
        if not (data_id or chunk_id or snippet):
            continue
        refs.append(
            EvidenceRef(
                data_id=data_id.group(1) if data_id else None,
                chunk_id=chunk_id.group(1) if chunk_id else None,
                document_name=doc.group(1).rstrip(".,)") if doc else None,
                snippet=snippet.group(1).strip() if snippet else None,
            )
        )
    return refs


def parse_recall(response: Any) -> RecallResult:
    """Normalize a Cloud recall/search response into a RecallResult.

    Concatenates the answer text across returned items (usually one) and collects
    all parsed citations. An empty list (e.g. RBAC-denied recall) yields an empty
    RecallResult with no references.
    """
    items = _iter_items(response)
    result = RecallResult(raw=response)
    answers: List[str] = []
    for item in items:
        text = item.get("text") or (item.get("raw") or {}).get("value") or ""
        result.kind = result.kind or item.get("kind")
        result.search_type = result.search_type or item.get("search_type")
        result.source = result.source or item.get("source")
        ds = item.get("dataset_id")
        if ds and ds not in result.dataset_ids:
            result.dataset_ids.append(ds)

        parts = _EVIDENCE_SPLIT_RE.split(text, maxsplit=1)
        answers.append(parts[0].strip())
        if len(parts) > 1:
            result.references.extend(_parse_evidence_block(parts[1]))

    result.answer = "\n\n".join(a for a in answers if a).strip()
    return result
