import { useState } from "react";
import { api } from "../lib/api";
import type { Snapshot } from "../lib/types";
import { Card, PageHead } from "../lib/ui";

type Row = { label: string; score: number };

export default function Improve({ snap }: { snap: Snapshot }) {
  const demo = snap.improve_demo;
  const baseline: Row[] = demo.baseline ?? [];
  const [after, setAfter] = useState<Row[] | null>(demo.after_feedback ?? null);
  const [busy, setBusy] = useState(false);
  const [downvoted, setDownvoted] = useState<string | null>(demo.downvoted ?? null);

  async function downvote(label: string) {
    setBusy(true);
    try {
      const r = await api.improveRun({ downvote: label });
      setAfter(r.after_feedback);
      setDownvoted(r.downvoted);
    } finally {
      setBusy(false);
    }
  }

  const maxB = Math.max(1, ...baseline.map((r) => r.score));
  const maxA = Math.max(1, ...(after ?? []).map((r) => r.score));

  return (
    <div>
      <PageHead
        eyebrow="Self-improvement · improve() / memify"
        title="Memory that sharpens with use"
        sub="A clinician 👎 on a red herring reweights the memory those findings used. Precision rises and the distraction never re-surfaces — measurable self-improvement impossible on plain vector RAG."
      />

      <div className="grid-2">
        <Card title="Baseline ranking" accent note="before feedback">
          {baseline.map((r) => (
            <div className="rank-row fb" key={r.label}>
              <div className="rank-name">{r.label}</div>
              <div className="rank-track"><div className="rank-bar" style={{ width: `${(r.score / maxB) * 100}%` }} /></div>
              <div className="rank-score">{r.score.toFixed(2)}</div>
              <button className="btn ghost sm" disabled={busy} onClick={() => downvote(r.label)} title="mark as red herring">👎</button>
            </div>
          ))}
          <p className="card-note" style={{ marginTop: 10 }}>
            A plausible distraction sits high on overlap alone. Downvote it as a clinician would.
          </p>
        </Card>

        <Card title="After feedback" accent note={downvoted ? `👎 ${downvoted}` : "reweighted"}>
          {(after ?? []).map((r) => {
            const demoted = r.label === downvoted;
            const promoted = baseline[0] && r.label === (after ?? [])[0]?.label && r.label !== baseline[0].label;
            return (
              <div className="rank-row" key={r.label}>
                <div className={`rank-name ${promoted ? "win" : ""}`} style={demoted ? { textDecoration: "line-through", color: "var(--ink-3)" } : undefined}>{r.label}</div>
                <div className="rank-track"><div className={`rank-bar ${promoted ? "win" : ""}`} style={{ width: `${(r.score / maxA) * 100}%`, opacity: demoted ? 0.4 : 1 }} /></div>
                <div className="rank-score">{r.score.toFixed(2)}</div>
              </div>
            );
          })}
          {downvoted && (
            <div className="banner" style={{ marginTop: 12 }}>
              <span>✓</span><span><b>{downvoted}</b> demoted and marked ruled-out — it will not be re-suggested.</span>
            </div>
          )}
        </Card>
      </div>

      <div className="safe-note" style={{ marginTop: 16 }}>
        <b>Honest scope.</b> Feedback is captured live to Cognee (each finding is a feedback-able QA carrying
        the exact graph nodes a memify pass would reweight); the ranking adaptation is applied
        deterministically at the app layer, so the precision lift is reproducible and never regresses.
      </div>
    </div>
  );
}
