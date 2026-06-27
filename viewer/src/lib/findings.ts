import type { Finding } from "./types";

export type Severity = Finding["severity"];

export const KIND_ORDER = [
  "prompt_injection",
  "exfiltration",
  "trajectory",
  "outcome",
] as const;

export const KIND_LABELS: Record<string, string> = {
  prompt_injection: "Prompt Injection",
  exfiltration: "Exfiltration",
  trajectory: "Trajectory",
  outcome: "Outcome",
};

// Severity -> Tailwind class fragments. Info/low share a muted gray-blue; medium
// amber; high orange; critical red. Each entry carries text, border, and a soft
// background tint so badges read consistently across the app.
export interface SeverityStyle {
  text: string;
  border: string;
  bg: string;
  dot: string;
}

const SEVERITY_STYLES: Record<Severity, SeverityStyle> = {
  info: {
    text: "text-slate-400",
    border: "border-slate-500/30",
    bg: "bg-slate-500/10",
    dot: "bg-slate-400",
  },
  low: {
    text: "text-slate-400",
    border: "border-slate-500/30",
    bg: "bg-slate-500/10",
    dot: "bg-slate-400",
  },
  medium: {
    text: "text-amber-400",
    border: "border-amber-500/30",
    bg: "bg-amber-500/10",
    dot: "bg-amber-400",
  },
  high: {
    text: "text-orange-400",
    border: "border-orange-500/30",
    bg: "bg-orange-500/10",
    dot: "bg-orange-400",
  },
  critical: {
    text: "text-red-400",
    border: "border-red-500/40",
    bg: "bg-red-500/10",
    dot: "bg-red-400",
  },
};

export function severityStyle(sev: Severity): SeverityStyle {
  return SEVERITY_STYLES[sev] ?? SEVERITY_STYLES.info;
}

const SEVERITY_RANK: Record<Severity, number> = {
  info: 0,
  low: 1,
  medium: 2,
  high: 3,
  critical: 4,
};

export function severityRank(sev: Severity): number {
  return SEVERITY_RANK[sev] ?? 0;
}

// A 0..1 score -> percent, with a health band for trajectory/outcome badges.
// Higher score = healthier (green); lower = riskier (red).
export function scoreToPercent(score: number): number {
  return Math.round(Math.max(0, Math.min(1, score)) * 100);
}

export function healthBand(score: number): SeverityStyle {
  const pct = scoreToPercent(score);
  if (pct >= 80) {
    return {
      text: "text-success",
      border: "border-success/30",
      bg: "bg-success/10",
      dot: "bg-success",
    };
  }
  if (pct >= 50) {
    return SEVERITY_STYLES.medium;
  }
  if (pct >= 25) {
    return SEVERITY_STYLES.high;
  }
  return SEVERITY_STYLES.critical;
}

export function groupByKind(findings: Finding[]): Record<string, Finding[]> {
  const out: Record<string, Finding[]> = {};
  for (const f of findings) {
    (out[f.kind] ??= []).push(f);
  }
  // Sort each group by severity (highest first), then by score descending.
  for (const k of Object.keys(out)) {
    out[k].sort(
      (a, b) =>
        severityRank(b.severity) - severityRank(a.severity) || b.score - a.score,
    );
  }
  return out;
}

// Step-level findings that flag injection or exfil, keyed by step id, so the
// timeline can show a warning icon on the right rows.
export function stepWarnings(findings: Finding[]): Map<number, Finding[]> {
  const out = new Map<number, Finding[]>();
  for (const f of findings) {
    if (f.step_id == null) continue;
    if (f.kind !== "prompt_injection" && f.kind !== "exfiltration") continue;
    const list = out.get(f.step_id) ?? [];
    list.push(f);
    out.set(f.step_id, list);
  }
  return out;
}

export function traceLevelFinding(
  findings: Finding[],
  kind: string,
): Finding | undefined {
  return findings
    .filter((f) => f.kind === kind && f.step_id == null)
    .sort((a, b) => severityRank(b.severity) - severityRank(a.severity))[0];
}
