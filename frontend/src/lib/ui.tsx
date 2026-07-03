import type { CSSProperties, ReactNode } from "react";
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

// Render a safe subset of markdown (**bold**, bullet lists, bold section headers) that the
// agents emit in their summaries — without pulling in a markdown dependency.
function renderInline(text: string, kp: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(<span key={`${kp}-t${i}`}>{text.slice(last, m.index)}</span>);
    out.push(<strong key={`${kp}-b${i}`}>{m[1]}</strong>);
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) out.push(<span key={`${kp}-t${i}`}>{text.slice(last)}</span>);
  return out;
}

export function Prose({ text, className, style }: { text: string; className?: string; style?: CSSProperties }) {
  const lines = cleanProse(text).split(/\n/);
  const blocks: ReactNode[] = [];
  let para: string[] = [];
  let list: string[] = [];
  let key = 0;
  const flushPara = () => {
    if (para.length) { const k = key++; blocks.push(<p className="prose-p" key={`p${k}`}>{renderInline(para.join(" "), `p${k}`)}</p>); para = []; }
  };
  const flushList = () => {
    if (list.length) { const k = key++; const items = list; blocks.push(<ul className="prose-ul" key={`u${k}`}>{items.map((li, i) => <li key={i}>{renderInline(li, `u${k}-${i}`)}</li>)}</ul>); list = []; }
  };
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    const bullet = /^[-*•]\s+(.*)$/.exec(line);
    const heading = /^\*\*(.+?)\*\*:?$/.exec(line);
    if (bullet) { flushPara(); list.push(bullet[1]); }
    else if (heading) { flushPara(); flushList(); const k = key++; blocks.push(<div className="prose-h" key={`h${k}`}>{heading[1]}</div>); }
    else { flushList(); para.push(line); }
  }
  flushPara(); flushList();
  return <div className={`prose prose-rich ${className ?? ""}`} style={style}>{blocks}</div>;
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
      <Prose text={f.summary} />
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
