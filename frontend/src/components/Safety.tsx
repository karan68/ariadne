import type { Snapshot } from "../lib/types";
import { Card, EvidenceList, PageHead, Pill } from "../lib/ui";

const SEV: Record<string, string> = { major: "red", moderate: "amber", minor: "blue" };

export default function Safety({ snap }: { snap: Snapshot }) {
  const s = snap.agents.safety;
  return (
    <div>
      <PageHead
        eyebrow="Safety agent"
        title="Polypharmacy & interaction watch"
        sub="Curated class rules applied only to the patient's actual drug set, then a recall cites the co-prescription. Flags, never auto-changes meds. Cross-prescriber duplication is caught straight from the graph."
      />

      <Card title="Grounded medication set" accent note={`${s.medications.length} distinct drugs`}>
        <div className="chips">{s.medications.map((m) => <Pill key={m} kind="violet">{m}</Pill>)}</div>
      </Card>

      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 18 }}>
        {s.alerts.map((a, i) => (
          <Card key={i} accent
            title={<span style={{ fontSize: 13.5, textTransform: "capitalize" }}>{a.kind}: {a.medications.join(" + ")}</span>}
            note={<Pill kind={SEV[a.severity] ?? "amber"}>{a.severity}</Pill>}
          >
            <div className="prose">{a.rationale}</div>
            <div style={{ marginTop: 6 }}><span className="cited-badge">✓ {a.evidence.length} cited (proves co-prescription)</span></div>
            <EvidenceList evidence={a.evidence} max={1} />
          </Card>
        ))}
      </div>

      <div className="safe-note" style={{ marginTop: 16 }}>
        <b>Flags, not orders.</b> Ariadne surfaces interactions for the clinician to review — it never
        changes a prescription. Rules encode fixed domain knowledge; the recall only cites that the drugs
        are genuinely co-prescribed.
      </div>
    </div>
  );
}
