// Thin API client for the Ariadne backend. All reads are snapshot-backed (instant);
// mutating calls (rbac check, improve, forget/run) hit deterministic/live endpoints.

import type { ImproveDemo, RbacView, Snapshot } from "./types";

const BASE = ""; // same-origin (Vite proxies /api to the backend in dev)

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`GET ${path} → ${res.status}`);
  return (await res.json()) as T;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${path} → ${res.status}`);
  return (await res.json()) as T;
}

export const api = {
  snapshot: () => get<Snapshot>("/api/snapshot"),
  health: () => get<{ status: string; snapshot: boolean; snapshot_generated_at: string | null }>("/health"),
  rbac: () => get<RbacView>("/api/rbac"),
  rbacCheck: (role: string, brain: string, patient_id = "odyssey") =>
    post<{
      role: string;
      brain: string;
      authorized: boolean;
      datasets: string[];
      denied: boolean;
      explanation: string;
    }>("/api/rbac/check", { role, brain, patient_id }),
  improve: () => get<ImproveDemo>("/api/improve"),
  improveRun: (opts: { downvote?: string; upvote?: string; rule_out?: string }) =>
    post<{
      downvoted: string | null;
      baseline: Array<{ label: string; score: number }>;
      after_feedback: Array<{ label: string; score: number }>;
    }>("/api/improve", opts),
  forgetRun: () => post<Record<string, unknown>>("/api/forget/run", {}),
};
