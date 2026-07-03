import type { Snapshot, TimelineEvent } from "../lib/types";
import { Card, FindingCard, PageHead, Pill } from "../lib/ui";

const VASCULAR = /claudicat|bruit|pulse|blood pressure|subclavian|renovascular|carotid|aort|vascul|cold/i;

function flavour(ev: TimelineEvent): "vascular" | "dx" | "" {
  if (/confirm/i.test(ev.description) && /takayasu/i.test(ev.description)) return "dx";
  if (VASCULAR.test(ev.description)) return "vascular";
  return "";
}

export default function Timeline({ snap }: { snap: Snapshot }) {
  const t = snap.agents.timeline;
  const span = t.span;
  return (
    <div>
      <PageHead
        eyebrow="Timeline agent"
        title="The four-year arc"
        sub="A deterministic, date-ordered reconstruction from the graph's event dates — plus a cited narrative recall. Vascular signs and the confirmed diagnosis are highlighted along the thread."
      />

      <div className="stat-row" style={{ marginBottom: 18 }}>
        <div className="stat"><div className="stat-val">{t.events.length}</div><div className="stat-lbl">dated events</div></div>
        {span && <div className="stat"><div className="stat-val" style={{ fontSize: 18 }}>{span[0]} → {span[1]}</div><div className="stat-lbl">span reconstructed</div></div>}
        <div className="stat"><div className="stat-val" style={{ fontSize: 18 }}>{t.used_search_type}</div><div className="stat-lbl">recall route</div></div>
      </div>

      <div className="grid-2">
        <Card title="Event arc" accent note={`${t.events.length} events`}>
          <div className="tl">
            {t.events.map((ev, i) => (
              <div className={`tl-item ${flavour(ev)}`} key={i}>
                <span className="tl-date">{ev.date}</span>
                <span className="tl-type">{ev.type}</span>
                <div className="tl-desc">{ev.description}</div>
              </div>
            ))}
          </div>
        </Card>

        <div>
          <Card title="Cited narrative" accent note="recall + citations">
            {t.narrative ? <FindingCard f={t.narrative} /> : <p className="prose">No cited narrative surfaced.</p>}
          </Card>
          <Card title="Legend">
            <div className="chips">
              <Pill kind="thread">● vascular sign</Pill>
              <Pill kind="green">● confirmed diagnosis</Pill>
              <Pill>● routine event</Pill>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
