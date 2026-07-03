# Ariadne — Architecture & Diagrams

Plain-language explanation of how Ariadne works, with sequence diagrams for both doors
(Clinician and Patient) and the Cognee memory lifecycle. All diagrams are Mermaid — they
render directly on GitHub.

---

## The one-sentence version

> **Ariadne takes a patient's scattered medical history, pours it into one "memory brain" on
> Cognee Cloud, and lets six specialist AI agents read that brain to surface answers — every
> one traceable back to the note it came from.**

A patient sees 7 doctors over 4 years. Each doctor sees only their own slice and gives a
plausible-but-wrong label. **The clues were always there — just never in one place.** Ariadne
is the "one place," and the thread that connects the clues.

---

## The two doors

The same memory brain, two very different users:

| Door | Who | What they do |
| --- | --- | --- |
| 🩺 **Clinician** | Doctor | *Reads* the brain — briefing, timeline, differential, trials, safety, prior-auth — and gives feedback that sharpens it. |
| 🙋 **Patient** | The data owner | *Controls* the brain — sees their own records, grants/denies who can read it (RBAC), and permanently deletes records (forget-with-proof). |

---

## System architecture

```mermaid
flowchart TB
    subgraph Front["🖥️ One role-switched app (React + Vite)"]
        Pat["🙋 Patient door"]
        Doc["🩺 Clinician door"]
    end

    API["⚙️ FastAPI API (app/main.py)<br/>snapshot-backed reads · live POSTs opt-in"]

    subgraph Agents["🤖 Six cited specialist agents (each its own principal)"]
        A1["Timeline"]; A2["Connections"]; A3["Trials"]
        A4["Safety"]; A5["Briefing"]; A6["Justify"]
    end

    subgraph Cloud["🧠 COGNEE CLOUD (hosted)"]
        B1["patient-{id}-clinical brain"]
        B2["reference-literature brain"]
        B3["reference-trials brain"]
        Feat["Custom clinical ontology · RBAC roles + grants<br/>Sessions (tokens/cost/audit) · improve() · forget()"]
    end

    Pat --> API
    Doc --> API
    API --> Agents
    Agents -->|recall / improve / forget| Cloud
    B1 --- Feat
```

---

## The Cognee lifecycle — the four verbs

Everything Ariadne does maps to Cognee's four memory operations:

```mermaid
flowchart LR
    A["📥 remember()<br/>ingest notes,<br/>labs, docs"] --> B["🧠 Cognee Cloud<br/>knowledge graph<br/>(one per patient)"]
    B --> C["🔎 recall()<br/>6 agents ask<br/>cited questions"]
    C --> D["📈 improve()<br/>doctor's feedback<br/>reweights memory"]
    D --> B
    B --> E["🗑️ forget()<br/>patient deletes<br/>a record — for real"]
    E --> B
```

- **remember()** → the patient's messy history becomes structured graph nodes + edges.
- **recall()** → agents ask questions; Cognee auto-routes between semantic similarity and deep
  graph traversal.
- **improve()** → a doctor downvotes a wrong lead; it's demoted and never comes back.
- **forget()** → the patient surgically deletes a record; it's genuinely gone (15 nodes → 6).

---

## Sequence diagram — 🩺 Clinician door

The doctor opens the app before a visit and reads the patient's story:

```mermaid
sequenceDiagram
    participant Doc as 🩺 Clinician
    participant UI as Ariadne UI
    participant API as Ariadne API
    participant Agents as 6 Cited Agents
    participant Cog as 🧠 Cognee Cloud

    Doc->>UI: Open patient (Clinician door)
    UI->>API: GET /api/snapshot
    API-->>UI: full cited snapshot
    Note over API,Cog: reads are snapshot-backed<br/>(real captured Cloud data — instant, demo-proof)

    Doc->>UI: Briefing
    UI-->>Doc: 10-sec one-pager, every line cited ✓

    Doc->>UI: Connections (differential)
    Note over Agents,Cog: recall() → phenotype-driven<br/>ranking over literature brain
    UI-->>Doc: Takayasu #1, red-thread to source notes

    Doc->>UI: Time-travel slider → 2022-08-05
    UI-->>Doc: "flaggable 18 months earlier" (emerges, not hand-set)

    Doc->>UI: 👎 downvote wrong lead (Lymphoma)
    UI->>API: POST /api/improve
    API->>Cog: improve() — reweight
    API-->>Doc: Lymphoma demoted, ruled-out, won't resurface

    Doc->>UI: Sessions
    UI-->>Doc: per-agent audit log (who asked what, tokens, cost)
```

**In words:** doctor opens the patient → gets a cited pre-visit briefing → sees the differential
with a "red thread" proving each finding traces to a real note → the time-travel view shows it
was catchable 18 months sooner → the doctor downvotes a distraction and memory gets smarter →
every agent's work is logged for audit.

---

## Sequence diagram — 🙋 Patient door

The patient owns the brain and controls access:

```mermaid
sequenceDiagram
    participant Pat as 🙋 Patient
    participant UI as Ariadne UI
    participant API as Ariadne API
    participant Cog as 🧠 Cognee Cloud

    Pat->>UI: Open app (Patient door)
    UI->>API: GET /api/snapshot
    API-->>UI: my records + knowledge graph

    Note over Pat,Cog: --- Access control (RBAC) ---
    Pat->>UI: Access & forget → test "family" on clinical brain
    UI->>API: POST /api/rbac/check {role: family}
    API->>Cog: recall() as family principal
    Cog-->>API: [] (not authorized)
    API-->>Pat: ❌ DENIED before it reaches memory

    Pat->>UI: test "provider" on clinical brain
    UI->>API: POST /api/rbac/check {role: provider}
    API->>Cog: recall() as provider principal
    Cog-->>API: dataset granted
    API-->>Pat: ✅ GRANTED

    Note over Pat,Cog: --- Forget with proof ---
    Pat->>UI: Delete a mislabeled record → "Re-run live"
    UI->>API: POST /api/forget/run
    API->>Cog: forget(dataset)
    Cog-->>API: nodes 15 → 6, bad fact now un-recallable
    API-->>Pat: 🗑️ Proof: gone, unrelated facts survive
```

**In words:** the patient sees their own data → proves access control works live (family is
blocked, provider is allowed — enforced *before* memory is touched) → deletes a bad record and
gets **proof** it's truly gone (node count drops, the bad fact is no longer recallable,
everything else survives).

---

## Full lifecycle — how a patient's memory evolves over time

```mermaid
sequenceDiagram
    autonumber
    participant Sys as Ariadne
    participant Cog as 🧠 Cognee Cloud
    participant Doc as 🩺 Clinician
    participant Pat as 🙋 Patient

    Note over Sys,Cog: 1. BUILD THE MEMORY
    Sys->>Cog: remember(4 years of notes, labs, docs)
    Cog-->>Sys: one knowledge graph (nodes + edges)

    Note over Sys,Cog: 2. READ THE MEMORY
    Doc->>Cog: recall() via 6 cited agents
    Cog-->>Doc: cited answers + red-thread to sources

    Note over Sys,Cog: 3. SHARPEN THE MEMORY
    Doc->>Cog: improve() — feedback reweights
    Cog-->>Cog: wrong leads demoted permanently

    Note over Sys,Cog: 4. GOVERN THE MEMORY
    Pat->>Cog: RBAC — grant/deny readers per role
    Pat->>Cog: forget() — surgical, proven deletion

    Note over Sys,Cog: Memory carries context across<br/>infinite sessions — smarter each visit
```

---

## Why this is "Best Use of Cognee Cloud"

Every Cloud-only feature is **load-bearing**, not decoration:

| Cognee Cloud capability | How Ariadne depends on it |
| --- | --- |
| **Hybrid graph + vector memory** | The whole point — it *connects* clues that plain vector search can't. |
| **Multi-tenant RBAC** | The patient really controls who reads their brain (family denied, provider allowed). |
| **Agents as principals** | Each of the six agents runs under its own identity → every recall is attributable. |
| **Sessions** | Free observability/audit: who asked what, tokens, cost — per agent. |
| **improve() / memify** | Memory sharpens from clinician feedback and never regresses. |
| **forget()** | Records can be truly, provably erased on the patient's command. |
| **Custom ontology** | A clinical `graph_model` gives the memory medical structure (conditions, meds, findings, events). |

**The headline:** Ariadne turned 4 scattered years into one answer, **18 months sooner** — and
shows its work at every step.
