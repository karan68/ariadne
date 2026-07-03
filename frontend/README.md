# Ariadne — Frontend

Vite + React + TypeScript. **One app, role-switched** (Patient · Clinician) over the same
Cognee Cloud brains — the two front doors from the plan (§2), unified so the clinician-led
workflow and patient agency (consent / access / forget) live in one place.

## Run

```powershell
npm install
npm run dev        # http://localhost:5173  (proxies /api, /health, /config → :8000)
npm run build      # type-check + production bundle → dist/
npm run typecheck  # tsc --noEmit
```

The backend (`uvicorn app.main:app --port 8000`) must be running; a committed demo snapshot
means no live cloud is required for the UI to work.

## Layout

```
src/
  App.tsx              door switch + sidebar nav + panel routing
  lib/
    api.ts             API client (snapshot, rbacCheck, improveRun, forgetRun)
    types.ts           TS types mirroring the backend snapshot shapes
    ui.tsx             shared primitives (Card, Pill, Confidence, EvidenceList, FindingCard…)
    styles… (styles.css)  the dark clinical design system + "Ariadne thread" motif
  components/
    Overview  Timeline  Graph  Access                 (patient + shared)
    Briefing  Connections  RedThreadViz  TimeTravel
    Trials    Safety  Justify  Improve  Sessions       (clinician)
```

## Notes

- **Design system:** deep clinical slate with a crimson "thread of Ariadne" accent; every
  finding renders its confidence + cited sources; Cognee's inline `【…】` citation spans are
  stripped from prose (`cleanProse`) and shown separately as evidence.
- **Windows-on-ARM:** `package.json` `overrides` alias `rollup` → `@rollup/wasm-node` so the
  build/dev server work where the native rollup binary won't `dlopen`. No action needed.
