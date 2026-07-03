import { useEffect, useState } from "react";
import type { RedThread, RedThreadBundle } from "../lib/types";
import { Card, Pill } from "../lib/ui";

const TYPE_COLOR: Record<string, string> = {
  Symptom: "var(--thread-2)", Condition: "var(--green)", LabResult: "var(--blue)",
  Medication: "var(--violet)", LiteraturePattern: "var(--amber)",
  ClinicalKnowledgeGraph: "var(--ink-3)", ReferenceLiteratureGraph: "var(--ink-3)",
  DocumentChunk: "var(--teal)", TextDocument: "var(--amber)",
};

interface StageNode { id: string; label: string; type: string }

// Reconstruct the ordered provenance stages from a thread's hops:
// anchor → container → chunk → document.
function stages(t: RedThread): StageNode[] {
  const out: StageNode[] = [{ id: t.anchor.id, label: t.anchor.label, type: t.anchor.type }];
  for (const h of t.hops) {
    out.push({ id: h.source_id, label: h.source_label || h.source_type, type: h.source_type });
  }
  // de-dup consecutive + ensure the document terminal
  const seen = new Set<string>();
  const uniq = out.filter((n) => (seen.has(n.id) ? false : (seen.add(n.id), true)));
  if (t.document_label && !uniq.some((n) => n.id === t.document_id)) {
    uniq.push({ id: t.document_id ?? "doc", label: t.document_label, type: "TextDocument" });
  }
  return uniq;
}

function ThreadRow({ t, active, onClick }: { t: RedThread; active: boolean; onClick: () => void }) {
  const st = stages(t);
  const W = 640, H = 74, padX = 90;
  const gap = st.length > 1 ? (W - padX * 2) / (st.length - 1) : 0;
  const xs = st.map((_, i) => padX + i * gap);
  const y = H / 2;
  const [draw, setDraw] = useState(0);
  useEffect(() => { const id = requestAnimationFrame(() => setDraw(1)); return () => cancelAnimationFrame(id); }, []);

  const path = xs.map((x, i) => `${i === 0 ? "M" : "L"} ${x} ${y}`).join(" ");
  return (
    <div className={`card`} style={{ padding: "14px 16px", cursor: "pointer", borderColor: active ? "var(--thread)" : undefined }} onClick={onClick}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 4 }}>
        <div className="card-title" style={{ fontSize: 13.5 }}>
          <span className="dot" style={{ background: TYPE_COLOR[t.anchor.type] ?? "var(--thread)" }} />
          {t.anchor.label}
        </div>
        <Pill kind="teal">{st.length} real edges → source</Pill>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} style={{ overflow: "visible" }}>
        <path d={path} fill="none" stroke="var(--thread)" strokeWidth={2.5} strokeLinecap="round"
          strokeDasharray="900" strokeDashoffset={900 - draw * 900}
          style={{ transition: "stroke-dashoffset 1.1s ease", filter: "drop-shadow(0 0 4px var(--thread-glow))" }} />
        {st.map((n, i) => (
          <g key={n.id + i} transform={`translate(${xs[i]}, ${y})`}>
            <circle r={7} fill="var(--surface-2)" stroke={TYPE_COLOR[n.type] ?? "var(--thread-2)"} strokeWidth={2.5} />
            <text x={0} y={i % 2 === 0 ? -16 : 26} textAnchor="middle" fontSize={10.5} fill="var(--ink-2)" style={{ fontWeight: 600 }}>
              {n.label.length > 20 ? n.label.slice(0, 19) + "…" : n.label}
            </text>
            <text x={0} y={i % 2 === 0 ? -28 : 38} textAnchor="middle" fontSize={8.5} fill="var(--ink-4)" style={{ textTransform: "uppercase", letterSpacing: 0.4 }}>
              {n.type}
            </text>
          </g>
        ))}
      </svg>
      {active && (
        <div className="evidence" style={{ marginTop: 6 }}>
          <div className="evidence-item">
            <span className="cite">{t.document_label} · chunk {t.chunk_id?.slice(0, 8)}</span> —{" "}
            <span className="evidence-quote">"{t.quote}…"</span>
          </div>
        </div>
      )}
    </div>
  );
}

export default function RedThreadViz({ bundle }: { bundle: RedThreadBundle }) {
  const threads = [...bundle.patient_threads.filter((t) => t.resolved), ...bundle.literature_threads.filter((t) => t.resolved)];
  const [active, setActive] = useState(0);
  return (
    <Card
      title={<>Cited red-thread — {bundle.condition}</>}
      accent
      note={
        <span>
          {bundle.all_edges_exist ? <Pill kind="green">✓ every hop is a real graph edge</Pill> : <Pill kind="red">edge check failed</Pill>}
        </span>
      }
    >
      <p className="card-note" style={{ marginBottom: 14 }}>
        Each finding is traced back over <b style={{ color: "var(--ink-2)" }}>real graph edges</b> to
        the exact source note it was extracted from — anchor → container → chunk → document. No hop is
        inferred; the UI can only draw edges the graph actually contains.
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {threads.map((t, i) => (
          <ThreadRow key={t.anchor.id} t={t} active={active === i} onClick={() => setActive(i)} />
        ))}
      </div>
      {bundle.unresolved_anchors.length > 0 && (
        <div className="safe-note" style={{ marginTop: 12 }}>
          {bundle.unresolved_anchors.length} anchor(s) could not be traced to a source document and are{" "}
          <b>excluded</b> (never given a fabricated citation).
        </div>
      )}
    </Card>
  );
}
