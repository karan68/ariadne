import type { Snapshot } from "../lib/types";
import { Card, EvidenceList, PageHead, Prose } from "../lib/ui";

export default function Briefing({ snap }: { snap: Snapshot }) {
  const b = snap.agents.briefing.brief;
  return (
    <div>
      <PageHead
        eyebrow="Briefing agent"
        title="10-second pre-visit brief"
        sub="Everything the next clinician needs, derived only from cited memory — no re-asking the patient to retell four years of history. Every line traces to a source note."
      />

      <div className="grid-2">
        <Card title="Active picture" accent note="cited summary">
          <Prose text={b.summary} />
          {snap.agents.briefing.brief.findings[0] && (
            <EvidenceList evidence={snap.agents.briefing.brief.findings[0].evidence} max={3} />
          )}
        </Card>

        <div>
          <Card title="Open questions to resolve" accent note={`${b.open_questions.length}`}>
            <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>
              {b.open_questions.map((q, i) => (
                <li key={i} style={{ color: "var(--ink-2)", fontSize: 13.5 }}>{q}</li>
              ))}
            </ul>
          </Card>
          <Card title="Milestones" note={`${b.timeline_highlights.length}`}>
            <div className="tl" style={{ marginTop: 4 }}>
              {b.timeline_highlights.map((ev, i) => (
                <div className={`tl-item ${/confirm/i.test(ev.description) ? "dx" : ""}`} key={i}>
                  <span className="tl-date">{ev.date}</span>
                  <div className="tl-desc">{ev.description}</div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      <div className="banner" style={{ marginTop: 18 }}>
        <span>ℹ️</span>
        <span>Brief assembled from {snap.agents.briefing.event_count} dated events. Suppressed (uncited) sections: {snap.agents.briefing.suppressed.length || "none"}.</span>
      </div>
    </div>
  );
}
