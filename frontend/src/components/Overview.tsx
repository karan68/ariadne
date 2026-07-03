import type { Snapshot } from "../lib/types";
import { Card, PageHead, Pill } from "../lib/ui";

export default function Overview({ snap, door }: { snap: Snapshot; door: string }) {
  const h = snap.hero;
  const tt = snap.timetravel;
  const conn = snap.agents.connections;
  return (
    <div>
      <PageHead
        eyebrow={door === "patient" ? "Your health passport" : "Patient overview"}
        title={door === "patient" ? `Hello, ${h.display_name}` : h.display_name}
        sub={
          door === "patient"
            ? "This is your memory — every record you own, connected into one living picture. You decide who sees it."
            : "A longitudinal clinical memory graph. Every insight below is cited back to the source note it came from."
        }
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat">
          <div className="stat-val thread">{tt.months_earlier} mo</div>
          <div className="stat-lbl">earlier the connected record could have flagged the pattern</div>
        </div>
        <div className="stat">
          <div className="stat-val">{snap.graph.n_nodes}</div>
          <div className="stat-lbl">memory nodes across {Object.keys(snap.graph.counts_by_type).length} types</div>
        </div>
        <div className="stat">
          <div className="stat-val">{snap.sessions.total_sessions}</div>
          <div className="stat-lbl">attributed agent sessions (audited)</div>
        </div>
        <div className="stat">
          <div className="stat-val">{conn.ranking.length}</div>
          <div className="stat-lbl">candidate patterns ranked from memory</div>
        </div>
      </div>

      <div className="grid-2">
        <Card title="Who this patient is" accent>
          <table className="table">
            <tbody>
              <tr><td style={{ color: "var(--ink-4)" }}>Synthetic ID</td><td>{h.id}</td></tr>
              <tr><td style={{ color: "var(--ink-4)" }}>Sex / born</td><td>{h.sex} · {h.year_of_birth}</td></tr>
              <tr><td style={{ color: "var(--ink-4)" }}>Context</td><td>{h.context}</td></tr>
              <tr><td style={{ color: "var(--ink-4)" }}>Confirmed diagnosis</td><td><Pill kind="green">{h.true_diagnosis}</Pill> on {h.true_diagnosis_date}</td></tr>
            </tbody>
          </table>
        </Card>

        <Card title="The through-line" accent note="the thread of Ariadne">
          <p className="prose">
            The signal was never in one note — it was scattered across years and specialists, each
            applying a plausible wrong label ("post-viral", "iron deficiency", "fibromyalgia",
            "anxiety"). Ariadne threads them into one graph, and the pattern that no single visit
            could see becomes obvious.
          </p>
          <div className="chips" style={{ marginTop: 12 }}>
            {conn.constellation.slice(0, 8).map((c) => <Pill key={c}>{c}</Pill>)}
          </div>
        </Card>
      </div>

      <div className="banner thread" style={{ marginTop: 18 }}>
        <span style={{ fontSize: 18 }}>🧵</span>
        <span>
          Snapshot generated {new Date(snap.generated_at).toLocaleString()} · condition{" "}
          <b>{snap.condition}</b> · every panel is backed by a real Cognee Cloud recall with
          traceable citations.
        </span>
      </div>
    </div>
  );
}
