import type { Snapshot } from "../lib/types";
import { Card, PageHead, Pill } from "../lib/ui";

const CLINICAL_TYPES = [
  "Symptom", "LabResult", "Condition", "Medication", "Encounter",
  "Provider", "ImagingStudy", "Procedure", "GeneVariant",
];

export default function Graph({ snap }: { snap: Snapshot }) {
  const g = snap.graph;
  const counts = g.counts_by_type ?? {};
  const clinical = CLINICAL_TYPES.map((t) => [t, counts[t] ?? 0] as const).filter(([, n]) => n > 0);
  const maxN = Math.max(1, ...clinical.map(([, n]) => n));
  return (
    <div>
      <PageHead
        eyebrow="Your records · knowledge graph"
        title="Everything, connected"
        sub="Every record you've added is extracted into a structured graph of clinical concepts — symptoms, labs, conditions, medications — linked across time. This is the memory the agents reason over."
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat"><div className="stat-val">{g.n_nodes}</div><div className="stat-lbl">memory nodes</div></div>
        <div className="stat"><div className="stat-val">{g.n_edges}</div><div className="stat-lbl">connections</div></div>
        <div className="stat"><div className="stat-val">{Object.keys(counts).length}</div><div className="stat-lbl">node types</div></div>
      </div>

      <div className="grid-2">
        <Card title="Clinical concepts extracted" accent>
          {clinical.map(([t, n]) => (
            <div className="gcount" key={t}>
              <span style={{ fontSize: 13, color: "var(--ink-2)", width: 130 }}>{t}</span>
              <div style={{ flex: 1, margin: "0 12px" }}>
                <div className="gcount-bar" style={{ width: `${(n / maxN) * 100}%` }} />
              </div>
              <span className="mono" style={{ color: "var(--ink)" }}>{n}</span>
            </div>
          ))}
        </Card>

        <div>
          <Card title="How your memory is built" accent>
            <p className="prose">
              Each record you add is ingested into Cognee Cloud and structured into this graph via a
              custom clinical ontology. Concepts are normalized to standard vocabularies (RxNorm, LOINC,
              SNOMED, HPO) so patterns can be matched rigorously — not by keyword.
            </p>
            <div className="chips" style={{ marginTop: 12 }}>
              <Pill kind="violet">RxNorm</Pill>
              <Pill kind="blue">LOINC</Pill>
              <Pill kind="teal">SNOMED CT</Pill>
              <Pill kind="amber">HPO</Pill>
              <Pill kind="thread">Orphanet / OMIM</Pill>
            </div>
          </Card>
          <Card title="Your data, your rules">
            <p className="prose" style={{ fontSize: 13 }}>
              This graph lives in a brain <b>you own</b>. Providers see it only when you grant access,
              and <b>forget</b> is real deletion — see the Access panel to prove both.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}
