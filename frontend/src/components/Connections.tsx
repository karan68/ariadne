import type { Snapshot } from "../lib/types";
import { Card, EvidenceList, FindingCard, PageHead, Pill, Prose } from "../lib/ui";
import RedThreadViz from "./RedThreadViz";

export default function Connections({ snap }: { snap: Snapshot }) {
  const c = snap.agents.connections;
  const maxScore = Math.max(1, ...c.ranking.map((r) => r.score));
  return (
    <div>
      <PageHead
        eyebrow="Connections agent"
        title="Connecting the dots"
        sub="A phenotype-driven differential over a curated literature brain. The ranking is deterministic (large-vessel signs weighted ×3); the discrimination narrative and every candidate carry citations."
      />

      <div className="grid-2">
        <Card title="Ranked candidate patterns" accent note="deterministic overlap score">
          {c.ranking.map((r) => {
            const win = r.condition === c.top_condition;
            return (
              <div key={r.condition}>
                <div className="rank-row">
                  <div className={`rank-name ${win ? "win" : ""}`}>{r.condition}</div>
                  <div className="rank-track"><div className={`rank-bar ${win ? "win" : ""}`} style={{ width: `${(r.score / maxScore) * 100}%` }} /></div>
                  <div className="rank-score">{r.score}</div>
                </div>
                {win && (
                  <div className="chips" style={{ margin: "2px 0 8px 2px" }}>
                    {r.vascular_features.map((f) => <Pill key={f} kind="thread">🩸 {f}</Pill>)}
                    {r.matched_features.filter((f) => !r.vascular_features.includes(f)).slice(0, 4).map((f) => <Pill key={f}>{f}</Pill>)}
                  </div>
                )}
              </div>
            );
          })}
          <div className="safe-note">
            <b>Decision support, not a diagnosis.</b> The winning pattern accounts for the large-vessel
            signs the constitutional mimics cannot — but every item is framed as "consider / investigate".
          </div>
        </Card>

        <Card title="Phenotype from memory" accent note={`${c.patient_hpo.length} HPO terms`}>
          <p className="card-note" style={{ marginBottom: 10 }}>
            Derived from the patient's own symptom nodes — the constellation the ranking matches against.
          </p>
          <div className="chips">
            {c.constellation.map((f) => <Pill key={f}>{f}</Pill>)}
          </div>
          <div className="divider" />
          <div className="card-title" style={{ fontSize: 13, marginBottom: 8 }}>Cited discrimination</div>
          {c.narrative ? <FindingCard f={c.narrative} /> : <p className="prose">No cited narrative.</p>}
        </Card>
      </div>

      <div style={{ marginTop: 18 }}>
        <RedThreadViz bundle={snap.redthread} />
      </div>

      <Card title="Top cited candidates" accent>
        {c.candidates.slice(0, 3).map((cand) => (
          <div key={cand.id} style={{ padding: "10px 0", borderBottom: "1px solid var(--line-soft)" }}>
            <Prose text={cand.summary} />
            <div style={{ marginTop: 6 }}><span className="cited-badge">✓ {cand.evidence.length} cited</span></div>
            <EvidenceList evidence={cand.evidence} max={2} />
          </div>
        ))}
      </Card>
    </div>
  );
}
