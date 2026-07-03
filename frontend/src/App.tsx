import { useEffect, useMemo, useState } from "react";
import { api } from "./lib/api";
import type { Door, Snapshot } from "./lib/types";
import Overview from "./components/Overview";
import Timeline from "./components/Timeline";
import Graph from "./components/Graph";
import Access from "./components/Access";
import Briefing from "./components/Briefing";
import Connections from "./components/Connections";
import TimeTravel from "./components/TimeTravel";
import Trials from "./components/Trials";
import Safety from "./components/Safety";
import Justify from "./components/Justify";
import Improve from "./components/Improve";
import Sessions from "./components/Sessions";

interface NavDef { id: string; label: string; ico: string; badge?: string }

const PATIENT_NAV: NavDef[] = [
  { id: "overview", label: "Overview", ico: "🏠" },
  { id: "timeline", label: "My timeline", ico: "🧵" },
  { id: "graph", label: "My records", ico: "🗂️" },
  { id: "access", label: "Access & forget", ico: "🔐" },
];

const CLINICIAN_NAV: NavDef[] = [
  { id: "overview", label: "Overview", ico: "🏠" },
  { id: "briefing", label: "Briefing", ico: "📋" },
  { id: "timeline", label: "Timeline", ico: "🧵" },
  { id: "connections", label: "Connections", ico: "🔗", badge: "cited" },
  { id: "timetravel", label: "Time-travel", ico: "⏱️" },
  { id: "trials", label: "Trials", ico: "🧪" },
  { id: "safety", label: "Safety", ico: "⚠️" },
  { id: "justify", label: "Prior-auth", ico: "📑" },
  { id: "improve", label: "Improve", ico: "📈" },
  { id: "sessions", label: "Sessions", ico: "📡" },
];

export default function App() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [door, setDoor] = useState<Door>("clinician");
  const [panel, setPanel] = useState("overview");

  useEffect(() => {
    api.snapshot().then(setSnap).catch((e) => setError(String(e)));
  }, []);

  const nav = door === "patient" ? PATIENT_NAV : CLINICIAN_NAV;

  useEffect(() => {
    if (!nav.some((n) => n.id === panel)) setPanel("overview");
  }, [door]); // eslint-disable-line react-hooks/exhaustive-deps

  const content = useMemo(() => {
    if (!snap) return null;
    switch (panel) {
      case "overview": return <Overview snap={snap} door={door} />;
      case "timeline": return <Timeline snap={snap} />;
      case "graph": return <Graph snap={snap} />;
      case "access": return <Access snap={snap} />;
      case "briefing": return <Briefing snap={snap} />;
      case "connections": return <Connections snap={snap} />;
      case "timetravel": return <TimeTravel snap={snap} />;
      case "trials": return <Trials snap={snap} />;
      case "safety": return <Safety snap={snap} />;
      case "justify": return <Justify snap={snap} />;
      case "improve": return <Improve snap={snap} />;
      case "sessions": return <Sessions snap={snap} />;
      default: return <Overview snap={snap} door={door} />;
    }
  }, [snap, panel, door]);

  if (error) {
    return (
      <div className="center-load">
        <div style={{ fontSize: 40 }}>🧵</div>
        <div style={{ maxWidth: 460, textAlign: "center" }}>
          <h2 style={{ fontFamily: "var(--font-serif)", margin: "0 0 8px" }}>Snapshot not available</h2>
          <p style={{ color: "var(--ink-3)", fontSize: 14 }}>
            The backend has no demo snapshot yet ({error}). Build it with{" "}
            <span className="kbd">python -m scripts.build_snapshot</span> and start the API with{" "}
            <span className="kbd">uvicorn app.main:app</span>.
          </p>
        </div>
      </div>
    );
  }

  if (!snap) {
    return (
      <div className="center-load">
        <div className="spinner" />
        <div>Loading Ariadne memory…</div>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" />
          <div>
            <div className="brand-name">Ariadne</div>
            <div className="brand-sub">clinical memory · Cognee Cloud</div>
          </div>
        </div>

        <div className="door-switch">
          <button className={`door-btn patient ${door === "patient" ? "active" : ""}`} onClick={() => setDoor("patient")}>
            <span className="door-ico">🧑</span>Patient
          </button>
          <button className={`door-btn clinician ${door === "clinician" ? "active" : ""}`} onClick={() => setDoor("clinician")}>
            <span className="door-ico">🩺</span>Clinician
          </button>
        </div>

        <nav className="nav">
          <div className="nav-label">{door === "patient" ? "Your passport" : "Point-of-care"}</div>
          {nav.map((n) => (
            <button key={n.id} className={`nav-item ${panel === n.id ? "active" : ""}`} onClick={() => setPanel(n.id)}>
              <span className="nav-ico">{n.ico}</span>
              {n.label}
              {n.badge && <span className="nav-badge">{n.badge}</span>}
            </button>
          ))}
        </nav>

        <div style={{ marginTop: "auto", padding: "0 6px" }}>
          <div className="mono" style={{ fontSize: 10.5, lineHeight: 1.6 }}>
            {snap.hero.display_name}<br />
            {snap.condition}<br />
            {snap.graph.n_nodes} nodes · {snap.sessions.total_sessions} sessions
          </div>
        </div>
      </aside>

      <main className="main" key={panel + door}>
        {content}
      </main>
    </div>
  );
}
