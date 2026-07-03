# Ariadne eval harness

Deterministic, labeled clinical cases that score agent quality. This is a **product
requirement**, not an afterthought: each build phase has an eval gate that must be
green before the next phase starts (see `plan.md` §17).

## Layout (populated from P1 onward)

```
evals/
  cases/*.json     input records + expected findings/codes (golden labels)
  run_evals.py     scores metrics, writes a report; CI fails below thresholds
```

## Metrics by phase

- **P1 ingestion:** entity/edge extraction vs golden hero-graph snapshot;
  normalization coverage % (RxNorm/LOINC/SNOMED/HPO); entity-resolution dedupe;
  no orphan clinical nodes.
- **P2 agents:** citation-coverage = 100% (uncited findings suppressed); no-diagnosis
  lint = 0 violations; Connections precision@k + HPO-match recall; Trials
  precision/recall on eligibility; Timeline temporal-ordering accuracy.
- **P3 lifecycle:** RBAC isolation (family → `[]`, provider → answer); precision@k
  delta after `improve()` (must not regress); `forget()` before/after proof.
- **P4 signature:** time-travel excludes future-dated nodes; red-thread edges all
  exist in the graph.

Fixtures are deterministic so evals run without live LLM cost wherever possible.
