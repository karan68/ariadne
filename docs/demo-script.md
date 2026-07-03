# Ariadne — Demo Script (≈3 minutes, 8 beats)

> **Setup (once):** start the backend (`uvicorn app.main:app --port 8000`) and the frontend
> (`npm run dev`), open **http://localhost:5173**. The API is snapshot-backed, so every read is
> instant and can't break on a cold/contended cloud. The two *live* buttons (Access → "Re-run
> forget proof live", Improve 👎) hit the cloud/deterministic paths and are safe to click.

**The patient:** a synthetic young woman, four-year diagnostic odyssey, confirmed **Takayasu
arteritis** (large-vessel vasculitis) on 2024-03-01. Every panel is backed by a real Cognee
Cloud recall with citations.

---

### Beat 0 — The frame (15s)
> "Most diagnostic delay isn't missing data — it's **un-connected** data. This patient saw
> seven specialists over four years; each gave a plausible wrong label. Ariadne threads her
> whole record into one memory graph on Cognee Cloud and lets six cited agents read it
> side-by-side."

Point at the sidebar: **one app, two doors** — Clinician and Patient. Start on **Clinician**.

---

### Beat 1 — Briefing: the 10-second pre-visit one-pager (20s)
Open **Briefing**.
> "Before the visit even starts: active problems, key meds, most-recent status, and the open
> questions to resolve — **every line cited back to a source note**. No re-asking the patient
> to retell four years of history."

Show the ✓ cited badges + the confirmed-diagnosis milestone.

---

### Beat 2 — Timeline: the four-year arc (20s)
Open **Timeline**.
> "The same memory, as a date-ordered arc — reconstructed deterministically from the graph's
> event dates. Watch the through-line: the **vascular signs** (crimson) accumulate, then the
> **confirmed diagnosis** (green) lands in 2024."

54 dated events, 2021 → 2024, with a cited narrative alongside.

---

### Beat 3 — Connections + the red-thread (30s)
Open **Connections**.
> "Now the reasoning. Ariadne derives the patient's phenotype from her own symptom nodes and
> runs a phenotype-driven differential over a curated literature brain. **Takayasu wins** — it
> accounts for the large-vessel signs the constitutional mimics can't."

Point at the ranking bars (Takayasu 9 · GCA 6 · …). Then scroll to the **red-thread**:
> "This is the anti-hallucination guarantee: every finding traces back over **real graph
> edges** to the exact source-document chunk that backs it. Click a thread — there's the
> verbatim quote from the 2022 note. The UI *cannot* draw an edge the graph doesn't contain."

---

### Beat 4 — Time-travel: the mic-drop (30s)
Open **Time-travel**. Drag the slider from 2021 toward 2024.
> "Here's the counterfactual. At each past date we scan only the notes that existed by then,
> rebuild the phenotype, and re-run the **identical** ranking. Watch Takayasu become the clear
> leader… and here —" (stop at **2022-08-05**) "— the first genuine **vascular sign** appears.
> The connected memory could have flagged large-vessel vasculitis **18 months before** the
> real diagnosis. That number isn't hand-set — it *emerges* from the computation, and the flag
> honestly requires a real vascular sign, not vague overlap."

---

### Beat 5 — Trials: eligibility with the deciding criterion cited (20s)
Open **Trials**.
> "Three open studies match her confirmed diagnosis and age — each with the **deciding
> criterion cited**. And note the discipline: the paediatric Takayasu trial is correctly
> **excluded** — right disease, wrong age. It shows *unmet* criteria too; the clinician
> confirms."

---

### Beat 6 — Improve: memory that sharpens (20s)
Open **Improve**. Click 👎 on the plausible red herring (**Lymphoma**).
> "A clinician downvotes a distraction. The ranking reweights, Lymphoma is demoted and marked
> ruled-out — **it will never re-surface**. Precision rises and never regresses. This
> feedback-weighted memory is the moat: impossible on plain vector RAG."

---

### Beat 7 — Patient door: ownership, RBAC, and forget-with-proof (25s)
Switch the door to **Patient**, open **Access & forget**.
> "The patient owns this brain. Let's prove access control **live**: role = **family**, brain =
> **clinical** → Run recall →" (it returns `[]`) "**denied**, before it ever reaches memory.
> Switch to **provider** → the dataset is granted. Real roles and grants on the Cognee tenant."

Then the forget card:
> "And **forget is real deletion**. Here's a mislabeled record removed surgically — 15 nodes to
> 6, the bad fact flips from recallable to gone, and unrelated concepts survive. Click
> *Re-run live* to prove it against a throwaway dataset in real time."

---

### Beat 8 — Sessions: observability + audit (15s)
Switch back to **Clinician**, open **Sessions**.
> "Finally, every agent recall is attributed — a per-agent audit log of who asked what and what
> memory answered, with tokens and cost. Full observability, for free, because each agent runs
> under its own session id."

---

### Close (10s)
> "One patient-owned memory graph on Cognee Cloud. Six cited agents. RBAC, Sessions, improve,
> and forget — every Cloud lever load-bearing. Ariadne turns four scattered years into one
> answer, 18 months sooner — and shows its work every step of the way."

---

## Fallback / resilience notes
- **Everything on screen is snapshot-backed** — no live latency, no cold-graph risk during the
  talk. The snapshot is real captured live data (`app/demo/snapshot.json`).
- The two **live** buttons (forget re-run, improve 👎) degrade gracefully: improve is
  deterministic (always works); the live forget re-run takes ~40-60s and falls back to the
  captured proof if the shared tenant is contended.
- If the backend snapshot is missing, the UI shows a clear "build the snapshot" message rather
  than a broken screen.
