"""Ariadne evaluation harness.

Deterministic, thresholded quality gates run per build phase. Each eval returns a
structured result (checks + metrics); `run_evals.py` prints a report, writes JSON,
and exits non-zero if any gating check fails.
"""
