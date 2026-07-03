# Ariadne

**A clinical memory & insight layer on [Cognee](https://docs.cognee.ai/).**
Ariadne's thread guides clinicians and patients out of the diagnostic labyrinth.

Most diagnostic delay isn't missing data — it's **un-connected** data scattered across
years, providers, and documents nobody read side-by-side. Ariadne ingests the whole
mess into **one longitudinal knowledge graph per patient**, then a swarm of narrow,
**cited** specialist agents reconstruct the timeline, connect the dots to medical
literature (differential *support*, never diagnosis), match open clinical trials, and
check medication safety — every finding traceable to its source. The patient **owns**
the brain and grants each provider access; `forget()` is real deletion.

> Decision support only. Ariadne surfaces *questions to investigate* and is always
> human-in-the-loop. It never states a diagnosis.

## Why Cognee Cloud

Ariadne leans on the Cloud-exclusive stack — multi-tenant RBAC (agents + humans as
principals), Sessions observability, hosted persistence — plus the full memory
lifecycle: `remember() → recall() → improve() → forget()`. The moat is **temporal +
graph + feedback-weighted memory that measurably sharpens** over time, which is
impossible on plain vector RAG.

## Repository layout

```
ariadne/
  backend/            FastAPI + Cognee SDK (local OSS now, Cognee Cloud via serve())
    app/
      config.py       runtime config + local/cloud switch + dataset naming
      ontology.py     clinical graph_model + custom extraction prompt
      models.py       Pydantic contracts + citation/no-diagnosis guardrails
      cognee_client.py  serve/remember/recall/search/improve/forget wrappers (+ mock)
      main.py         FastAPI app (health/config; agents added per phase)
    tests/            deterministic unit/contract tests (mock Cognee)
    evals/            labeled clinical cases + metrics harness (added P1+)
  frontend/           Vite + React + TS (patient | clinician), added P5
```

## Local development

The same `cognee` SDK targets local open-source Cognee or Cognee Cloud — only
`cognee.serve()` differs. We build **local-first** using the sibling `cognee` clone
plus Ollama embeddings, then flip to Cloud by setting `COGNEE_BASE_URL` + `COGNEE_API_KEY`.

```powershell
cd ariadne\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # installs cognee editable from ..\..\cognee
copy .env.example .env                     # then fill in provider keys
pytest                                     # P0 unit/contract gate
uvicorn app.main:app --reload              # http://127.0.0.1:8000/health
```

Set `COGNEE_BASE_URL` / `COGNEE_API_KEY` in `.env` to run against Cognee Cloud instead
of the local store.

## Status

Under active phase-wise construction (P0 foundations → P6 polish). See the build plan
and phase-wise eval gates in the session `plan.md`.
