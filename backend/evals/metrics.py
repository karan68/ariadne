"""Pure scoring functions for the Ariadne eval harness.

Deterministic, dependency-free, and unit-tested offline so the swarm scorecard can be
computed without live LLM cost where possible. Every function returns a float in [0, 1]
(or a non-negative count), so a case threshold is a simple numeric comparison.
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Sequence, Set, TypeVar

T = TypeVar("T")


def set_precision(retrieved: Iterable[T], gold: Iterable[T]) -> float:
    """|retrieved ∩ gold| / |retrieved|. Empty retrieved -> 1.0 (nothing wrong surfaced)."""
    r, g = set(retrieved), set(gold)
    if not r:
        return 1.0
    return round(len(r & g) / len(r), 4)


def set_recall(retrieved: Iterable[T], gold: Iterable[T]) -> float:
    """|retrieved ∩ gold| / |gold|. Empty gold -> 1.0 (nothing required)."""
    r, g = set(retrieved), set(gold)
    if not g:
        return 1.0
    return round(len(r & g) / len(g), 4)


def f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return round(2 * precision * recall / (precision + recall), 4)


def precision_at_k(ranked: Sequence[T], relevant: Iterable[T], k: int) -> float:
    """Fraction of the top-k ranked items that are in the relevant set.

    k is clamped to len(ranked); an empty ranking scores 0.0 (nothing relevant surfaced)."""
    if k <= 0 or not ranked:
        return 0.0
    top = list(ranked)[:k]
    rel = set(relevant)
    hits = sum(1 for x in top if x in rel)
    return round(hits / len(top), 4)


def temporal_ordering_accuracy(dates: Sequence[str]) -> float:
    """Fraction of adjacent pairs that are non-decreasing (a perfectly ordered timeline
    scores 1.0). Fewer than two dated items is trivially ordered -> 1.0."""
    seq = [d for d in dates if d]
    if len(seq) < 2:
        return 1.0
    ok = sum(1 for a, b in zip(seq, seq[1:]) if a <= b)
    return round(ok / (len(seq) - 1), 4)


def citation_coverage(items: Sequence[T],
                      evidence_of: Callable[[T], Iterable] = lambda x: x.evidence) -> float:
    """Fraction of surfaced items that carry >= 1 citation. No items -> 1.0 (nothing
    uncited was surfaced); the harness gates non-triviality with a separate count check."""
    if not items:
        return 1.0
    cited = sum(1 for it in items if list(evidence_of(it) or []))
    return round(cited / len(items), 4)


def lint_violation_count(texts: Iterable[str],
                         finder: Callable[[str], Iterable]) -> int:
    """Number of texts that trip the no-diagnosis lint (each `finder(text)` returns the
    matched phrases; a non-empty result counts as one violating text)."""
    return sum(1 for t in texts if t and list(finder(t)))


def coverage_fraction(present: int, expected: int) -> float:
    """present / expected, capped at 1.0. expected <= 0 -> 1.0."""
    if expected <= 0:
        return 1.0
    return round(min(present / expected, 1.0), 4)
