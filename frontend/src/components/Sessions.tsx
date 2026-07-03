import type { Snapshot } from "../lib/types";
import { Card, PageHead, Pill } from "../lib/ui";

const AGENT_ORDER = ["timeline", "connections", "trials", "briefing", "safety", "justify"];

export default function Sessions({ snap }: { snap: Snapshot }) {
  const s = snap.sessions;
  const rows = AGENT_ORDER.map((a) => s.by_agent[a]).filter(Boolean);
  const maxSess = Math.max(1, ...rows.map((r) => r.session_count));
  return (
    <div>
      <PageHead
        eyebrow="Observability · Cognee Sessions"
        title="Every agent recall, attributed"
        sub="Because each agent runs under a structured session id, the Sessions plane is a per-agent audit log with zero extra instrumentation — who asked what, and what memory answered."
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat"><div className="stat-val">{s.total_sessions}</div><div className="stat-lbl">total sessions</div></div>
        <div className="stat"><div className="stat-val">{s.tokens_total.toLocaleString()}</div><div className="stat-lbl">tokens</div></div>
        <div className="stat"><div className="stat-val">{s.agents_seen.length}/6</div><div className="stat-lbl">agents attributed</div></div>
        <div className="stat"><div className="stat-val">{s.all_agents_attributed ? "✓" : "—"}</div><div className="stat-lbl">full coverage</div></div>
      </div>

      <Card title="Per-agent attribution" accent>
        <table className="tbl">
          <thead><tr><th>Agent</th><th>Sessions</th><th>Runs</th><th>Errors</th><th>Patients</th><th>Last activity</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.agent}>
                <td><Pill kind="teal">{r.agent}</Pill></td>
                <td>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div className="mini-track"><div className="mini-bar" style={{ width: `${(r.session_count / maxSess) * 100}%` }} /></div>
                    <span className="mono">{r.session_count}</span>
                  </div>
                </td>
                <td className="mono">{r.run_count}</td>
                <td className="mono" style={{ color: r.error_count ? "var(--red)" : "var(--ink-3)" }}>{r.error_count}</td>
                <td>{r.patients.join(", ")}</td>
                <td className="mono" style={{ fontSize: 11.5 }}>{(r.last_activity ?? "").replace("T", " ").slice(0, 16)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      {s.cost_by_model.length > 0 && (
        <Card title="Cost by model" note="aggregate">
          <table className="tbl">
            <thead><tr><th>Model</th><th>Sessions</th><th>Tokens in</th><th>Tokens out</th></tr></thead>
            <tbody>
              {s.cost_by_model.map((m, i) => (
                <tr key={i}>
                  <td className="mono">{String(m.model)}</td>
                  <td className="mono">{String(m.session_count ?? "—")}</td>
                  <td className="mono">{Number(m.tokens_in ?? 0).toLocaleString()}</td>
                  <td className="mono">{Number(m.tokens_out ?? 0).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
