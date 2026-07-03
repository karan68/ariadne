import type { ReactNode } from "react";
import type { EvidenceRef, Finding } from "./types";

export function Card({ title, note, children, accent }: {
  title?: ReactNode; note?: ReactNode; children: ReactNode; accent?: boolean;
}) {
  return (
    <div className="card fade-in">
      {(title || note) && (
        <div className="card-head">
          {title && <div className="card-title">{accent && <span className="dot" />}{title}</div>}
          {note && <div className="card-note">{note}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

export function Pill({ children, kind }: { children: ReactNode; kind?: string }) {
  return <span className={`pill ${kind ?? ""}`}>{children}</span>;
}

export function Confidence({ score, band }: { score: number; band?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, score)) * 100);
  return (
    <div className="conf" title={`confidence ${pct}%`}>
      <div className="conf-bar"><div className="conf-fill" style={{ width: `${pct}%` }} /></div>
      <span className="conf-txt">{band ?? ""} {pct}%</span>
    </div>
  );
}

// Strip Cognee's inline 【…】 citation spans for clean prose; the citations are shown
// separately in the evidence list.
export function cleanProse(text: string): string {
  return (text || "").replace(/【[^】]*】/g, "").replace(/\[\d+\]/g, "").replace(/[ \t]+\n/g, "\n").trim();
}

export function EvidenceList({ evidence, max = 3 }: { evidence: EvidenceRef[]; max?: number }) {
  if (!evidence?.length) return null;
  return (
    <div className="evidence">
      {evidence.slice(0, max).map((e, i) => (
        <div className="evidence-item" key={i}>
          <span className="cite">{e.document_name ?? "doc"}{e.data_id ? ` · ${e.data_id.slice(0, 8)}` : ""}</span>
          {e.snippet && <> — <span className="evidence-quote">"{(e.snippet || "").slice(0, 130)}…"</span></>}
        </div>
      ))}
      {evidence.length > max && <div className="evidence-item" style={{ borderColor: "transparent" }}>+{evidence.length - max} more cited source(s)</div>}
    </div>
  );
}

export function FindingCard({ f }: { f: Finding }) {
  return (
    <div>
      <div className="prose">{cleanProse(f.summary)}</div>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 10 }}>
        <Confidence score={f.confidence_score} band={f.confidence} />
        <span className="cited-badge">✓ {f.evidence.length} cited source{f.evidence.length === 1 ? "" : "s"}</span>
      </div>
      <EvidenceList evidence={f.evidence} />
    </div>
  );
}

export function SafetyNote({ children }: { children: ReactNode }) {
  return <div className="safe-note">{children}</div>;
}

export function PageHead({ eyebrow, title, sub }: { eyebrow: string; title: string; sub: string }) {
  return (
    <div className="page-head">
      <div className="eyebrow">{eyebrow}</div>
      <h1 className="page-title">{title}</h1>
      <p className="page-sub">{sub}</p>
    </div>
  );
}
