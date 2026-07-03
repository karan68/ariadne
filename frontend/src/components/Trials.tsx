import type { Snapshot } from "../lib/types";
import { Card, EvidenceList, PageHead, Pill } from "../lib/ui";

export default function Trials({ snap }: { snap: Snapshot }) {
  const t = snap.agents.trials;
  return (
    <div>
      <PageHead
        eyebrow="Trials agent"
        title="Open studies, matched to the record"
        sub="Deterministic eligibility on the rigorously checkable axes (confirmed condition + age band parsed from each trial's own criteria), with the deciding criterion cited. Unmet criteria are shown too — assistive, clinician-confirmed."
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat"><div className="stat-val" style={{ color: "var(--green)" }}>{t.eligible_ids.length}</div><div className="stat-lbl">eligible</div></div>
        <div className="stat"><div className="stat-val" style={{ color: "var(--red)" }}>{t.ineligible_ids.length}</div><div className="stat-lbl">not eligible</div></div>
        <div className="stat"><div className="stat-val">{t.hero_age ?? "—"}</div><div className="stat-lbl">patient age</div></div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {t.matches.map((m) => (
          <Card key={m.nct_id} accent
            title={<span style={{ fontSize: 13.5 }}>{m.nct_id} · {m.title}</span>}
            note={m.eligible ? <Pill kind="green">✓ eligible</Pill> : <Pill kind="red">not eligible</Pill>}
          >
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div className="card-title" style={{ fontSize: 12, marginBottom: 6, color: "var(--green)" }}>Matched</div>
                {m.matched_criteria.length
                  ? m.matched_criteria.map((c, i) => <div key={i} className="prose" style={{ fontSize: 12.5 }}>• {c}</div>)
                  : <span className="card-note">—</span>}
              </div>
              <div style={{ flex: 1, minWidth: 220 }}>
                <div className="card-title" style={{ fontSize: 12, marginBottom: 6, color: "var(--red)" }}>Unmet / excluded</div>
                {m.unmet_criteria.length
                  ? m.unmet_criteria.map((c, i) => <div key={i} className="prose" style={{ fontSize: 12.5 }}>• {c}</div>)
                  : <span className="card-note">—</span>}
              </div>
            </div>
            <div className="divider" />
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <Pill kind="blue">deciding: {m.deciding_criterion}</Pill>
              <span className="cited-badge">✓ {m.evidence.length} cited</span>
            </div>
            <EvidenceList evidence={m.evidence} max={1} />
          </Card>
        ))}
      </div>
    </div>
  );
}
