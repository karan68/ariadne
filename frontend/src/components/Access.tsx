import { Fragment, useState } from "react";
import { api } from "../lib/api";
import type { ForgetProof, Snapshot } from "../lib/types";
import { Card, PageHead, Pill } from "../lib/ui";

const BRAINS = ["clinical", "literature", "trials"];
const ROLES = ["provider", "family"];

export default function Access({ snap }: { snap: Snapshot }) {
  const rbac = snap.rbac;
  const [role, setRole] = useState("family");
  const [brain, setBrain] = useState("clinical");
  const [result, setResult] = useState<null | { authorized: boolean; datasets: string[]; explanation: string }>(null);
  const [busy, setBusy] = useState(false);

  const [forget, setForget] = useState<ForgetProof>(snap.forget_demo);
  const [forgetBusy, setForgetBusy] = useState(false);

  async function runCheck() {
    setBusy(true);
    try {
      const r = await api.rbacCheck(role, brain);
      setResult(r);
    } catch {
      setResult(null);
    } finally {
      setBusy(false);
    }
  }

  async function runForget() {
    setForgetBusy(true);
    try {
      const r = (await api.forgetRun()) as unknown as ForgetProof;
      setForget(r);
    } catch {
      /* keep captured proof */
    } finally {
      setForgetBusy(false);
    }
  }

  return (
    <div>
      <PageHead
        eyebrow="Your data · access control"
        title="You own the brain"
        sub="Every provider you grant sees only what you allow. Family sees the general brain, never the clinical one — and you can prove it live. Forget is real deletion, with before/after proof."
      />

      <div className="grid-2">
        <Card title="Prove access control, live" accent>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", marginBottom: 12 }}>
            <select className="select" value={role} onChange={(e) => { setRole(e.target.value); setResult(null); }}>
              {ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <span className="card-note">recalls the</span>
            <select className="select" value={brain} onChange={(e) => { setBrain(e.target.value); setResult(null); }}>
              {BRAINS.map((b) => <option key={b} value={b}>{b}</option>)}
            </select>
            <span className="card-note">brain</span>
            <button className="btn primary" onClick={runCheck} disabled={busy}>{busy ? "Checking…" : "Run recall"}</button>
          </div>

          {result && (
            <div className={`banner ${result.authorized ? "" : "thread"}`}>
              {result.authorized ? (
                <><span>✓</span><span>Authorized — recall returns from <b>{result.datasets[0]}</b>.</span></>
              ) : (
                <><span>⛔</span><span>Denied — recall returns <code>[]</code>. {result.explanation}</span></>
              )}
            </div>
          )}
          <div className="safe-note" style={{ marginTop: 12 }}>
            Try <b>family → clinical</b>: the recall returns an empty list before it ever reaches memory.
            Isolation you can see, enforced at the app boundary over real Cognee Cloud grants.
          </div>
        </Card>

        <Card title="Access matrix" accent note="who can read what">
          <div className="rbac-grid" style={{ gridTemplateColumns: `120px repeat(${BRAINS.length}, 1fr)` }}>
            <div className="rbac-h" />
            {BRAINS.map((b) => <div className="rbac-h" key={b}>{b}</div>)}
            {["owner", ...ROLES].map((r) => (
              <Fragment key={r}>
                <div className="rbac-role">{r}</div>
                {BRAINS.map((b) => {
                  const allow = rbac.matrix[r]?.[b];
                  return <div key={`${r}-${b}`} className={`rbac-cell ${allow ? "allow" : "deny"}`}>{allow ? "read" : "—"}</div>;
                })}
              </Fragment>
            ))}
          </div>
          {rbac.report && (
            <p className="card-note" style={{ marginTop: 12 }}>
              Live on tenant: roles <b>{rbac.report.roles.map((r) => r.role_name).join(", ")}</b>,
              {" "}{rbac.report.agents.length} agent principals registered.
            </p>
          )}
        </Card>
      </div>

      <Card title="Forget, with proof" accent note="right to be forgotten">
        <p className="card-note" style={{ marginBottom: 12 }}>
          Deleting a mislabeled record surgically removes exactly its nodes — derived leads update,
          unrelated concepts survive. Proven on a disposable dataset (never the real record).
        </p>
        <div className="stat-row" style={{ marginBottom: 14 }}>
          <div className="stat"><div className="stat-val">{forget.nodes_before}</div><div className="stat-lbl">nodes before</div></div>
          <div className="stat"><div className="stat-val" style={{ color: "var(--thread-2)" }}>{forget.nodes_after}</div><div className="stat-lbl">nodes after (−{forget.nodes_removed})</div></div>
          <div className="stat"><div className="stat-val" style={{ color: "var(--green)" }}>{forget.is_surgical ? "✓ surgical" : "—"}</div><div className="stat-lbl">verdict</div></div>
        </div>
        <div className="forget-proof">
          <div className="forget-line"><span className="mono">{forget.probe_query}</span><Pill kind="red">before: {forget.probe_before}</Pill><span>→</span><Pill kind="green">after: {forget.probe_after}</Pill></div>
          <div className="forget-line"><span className="mono">{forget.unrelated_query}</span><Pill kind="green">survives: {forget.unrelated_after}</Pill></div>
        </div>
        <button className="btn primary" style={{ marginTop: 14 }} onClick={runForget} disabled={forgetBusy}>
          {forgetBusy ? "Re-deriving live (≈40s)…" : "Re-run forget proof live"}
        </button>
        {forget.live && <Pill kind="teal" >live re-derived</Pill>}
      </Card>
    </div>
  );
}
