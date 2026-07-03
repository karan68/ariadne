import type { Snapshot } from "../lib/types";
import { Card, cleanProse, EvidenceList, PageHead, Pill } from "../lib/ui";

export default function Justify({ snap }: { snap: Snapshot }) {
  const j = snap.agents.justify;
  return (
    <div>
      <PageHead
        eyebrow="Justify agent"
        title="Prior-authorisation evidence packet"
        sub="A payer needs proof of the indication, active disease, step-therapy already tried, and evidence for the requested drug — scattered across years of notes. Ariadne assembles it, every element cited. It assembles; it does not submit."
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat"><div className="stat-val" style={{ fontSize: 20 }}>{j.requested_drug}</div><div className="stat-lbl">requested therapy</div></div>
        <div className="stat"><div className="stat-val" style={{ fontSize: 20 }}>{j.indication}</div><div className="stat-lbl">indication</div></div>
        <div className="stat">
          <div className="stat-val" style={{ color: j.complete ? "var(--green)" : "var(--amber)" }}>{j.complete ? "Complete" : "Incomplete"}</div>
          <div className="stat-lbl">{j.elements.filter((e) => e.satisfied).length}/{j.elements.length} elements cited</div>
        </div>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {j.elements.map((el) => (
          <Card key={el.key} accent
            title={<span style={{ fontSize: 13.5 }}>{el.label}</span>}
            note={el.satisfied ? <Pill kind="green">✓ cited · {el.source}</Pill> : <Pill kind="amber">missing</Pill>}
          >
            <div className="prose">{cleanProse(el.content)}</div>
            <EvidenceList evidence={el.evidence} max={1} />
          </Card>
        ))}
      </div>

      <div className="safe-note" style={{ marginTop: 16 }}>
        <b>Assembles, does not submit.</b> Every element is drawn from the patient's own cited memory
        (the supporting-evidence element from the reference trials brain). A wrong premise would be
        caught by the grounded scaffold, not just the citation.
      </div>
    </div>
  );
}
