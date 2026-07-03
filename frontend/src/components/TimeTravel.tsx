import { useMemo, useState } from "react";
import type { Snapshot } from "../lib/types";
import { Card, PageHead, Pill } from "../lib/ui";

export default function TimeTravel({ snap }: { snap: Snapshot }) {
  const tt = snap.timetravel;
  const trace = tt.trace;
  const flagIdx = useMemo(
    () => Math.max(0, trace.findIndex((s) => s.date === tt.first_flag_date)),
    [trace, tt.first_flag_date]
  );
  const [idx, setIdx] = useState(flagIdx >= 0 ? flagIdx : trace.length - 1);
  const step = trace[idx];
  const maxScore = Math.max(1, ...(step?.ranking.map((r) => r.score) ?? [1]));
  const atFlag = step && tt.first_flag_date && step.date >= tt.first_flag_date;
  const pct = trace.length > 1 ? (idx / (trace.length - 1)) * 100 : 0;

  return (
    <div>
      <PageHead
        eyebrow="Time-travel counterfactual"
        title="When could memory have known?"
        sub="Reconstruct what the connected record supported at any past date. The phenotype is scanned from the notes that existed by then and the identical ranking is re-run — the 'months earlier' emerges from the computation, not fiat."
      />

      <div className="banner thread" style={{ marginBottom: 18 }}>
        <span style={{ fontSize: 20 }}>⏱️</span>
        <span>
          The connected memory supported flagging large-vessel vasculitis on{" "}
          <b style={{ color: "var(--thread-2)" }}>{tt.first_flag_date}</b> —{" "}
          <b style={{ color: "var(--thread-2)" }}>{tt.months_earlier} months</b> before the real
          diagnosis on {tt.true_diagnosis_date}.
        </span>
      </div>

      <Card title="Drag through time" accent note={`${trace.length} dated encounters`}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 6 }}>
          <div className="stat-val" style={{ fontFamily: "var(--font-mono)", fontSize: 22 }}>{step?.date}</div>
          {atFlag ? <Pill kind="thread">🩸 vascular flag reached</Pill> : <Pill>accumulating constitutional signal</Pill>}
        </div>
        <input
          type="range" min={0} max={trace.length - 1} value={idx}
          onChange={(e) => setIdx(Number(e.target.value))}
          style={{ background: `linear-gradient(90deg, var(--thread) ${pct}%, var(--surface-3) ${pct}%)` }}
        />
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
          <span className="mono">{trace[0]?.date}</span>
          <span className="mono">diagnosis {tt.true_diagnosis_date}</span>
        </div>
      </Card>

      <div className="grid-2" style={{ marginTop: 18 }}>
        <Card title="Ranking as of this date" accent>
          {(step?.ranking ?? []).map((r) => {
            const win = r.condition === step.top_condition;
            return (
              <div className="rank-row" key={r.condition}>
                <div className={`rank-name ${win ? "win" : ""}`}>{r.condition}</div>
                <div className="rank-track"><div className={`rank-bar ${win ? "win" : ""}`} style={{ width: `${(r.score / maxScore) * 100}%` }} /></div>
                <div className="rank-score">{r.score}</div>
              </div>
            );
          })}
          {step?.top_is_clear && (
            <div className="safe-note" style={{ marginTop: 10 }}>
              <b style={{ color: "var(--green)" }}>{step.top_condition}</b> is already the single leading
              explanation at this cutoff{step.has_vascular ? " — with a genuine vascular sign present." : "."}
            </div>
          )}
        </Card>

        <Card title="Phenotype known by then" accent note={`${step?.phenotype_count ?? 0} features`}>
          {step?.new_features.length > 0 && (
            <>
              <div className="card-title" style={{ fontSize: 12.5, marginBottom: 6 }}>New at {step.date}</div>
              <div className="chips" style={{ marginBottom: 12 }}>
                {step.new_features.map((f) => <Pill key={f} kind="amber">+ {f}</Pill>)}
              </div>
            </>
          )}
          <div className="card-title" style={{ fontSize: 12.5, marginBottom: 6 }}>Vascular discriminators present</div>
          <div className="chips">
            {step?.vascular_features.length
              ? step.vascular_features.map((f) => <Pill key={f} kind="thread">🩸 {f}</Pill>)
              : <span className="card-note">none yet — constitutional signal only</span>}
          </div>
          <div className="divider" />
          <p className="card-note">
            The headline flag is honest: it requires a real large-vessel sign, not constitutional
            overlap alone (the CDS-exemption "show the basis" bar).
          </p>
        </Card>
      </div>
    </div>
  );
}
