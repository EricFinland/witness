import { useState } from "react";
import { ChevronDown, ChevronRight, ShieldAlert } from "lucide-react";
import type { Finding } from "@/lib/types";
import { cn } from "@/lib/utils";
import {
  KIND_LABELS,
  KIND_ORDER,
  groupByKind,
  severityStyle,
} from "@/lib/findings";
import { JsonView } from "@/components/json-view";

interface Props {
  findings: Finding[];
  steps: { id: number; idx: number }[];
  // Jump to (and highlight) a step in the timeline when a step-level finding is
  // clicked. Receives the step's idx.
  onJumpToStep?: (idx: number) => void;
}

export function FindingsPanel({ findings, steps, onJumpToStep }: Props) {
  if (findings.length === 0) {
    return (
      <div className="rounded-md border border-border border-dashed p-8 text-center">
        <ShieldAlert size={18} className="text-fg-subtle mx-auto mb-2" />
        <p className="text-fg-muted text-sm">No findings for this trace.</p>
        <p className="text-fg-subtle text-xs mt-1">
          Run analysis to scan for injection, exfiltration, and trajectory issues.
        </p>
      </div>
    );
  }

  const grouped = groupByKind(findings);
  const stepIdToIdx = new Map(steps.map((s) => [s.id, s.idx]));
  // Keep known kinds in canonical order, then append any unexpected kinds.
  const kinds = [
    ...KIND_ORDER.filter((k) => grouped[k]?.length),
    ...Object.keys(grouped).filter(
      (k) => !KIND_ORDER.includes(k as (typeof KIND_ORDER)[number]),
    ),
  ];

  return (
    <div className="space-y-5">
      {kinds.map((kind) => (
        <section key={kind}>
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-fg-subtle mb-2">
            {KIND_LABELS[kind] ?? kind.replace(/_/g, " ")}{" "}
            <span className="text-fg-subtle font-normal">({grouped[kind].length})</span>
          </h3>
          <div className="space-y-2">
            {grouped[kind].map((f) => (
              <FindingCard
                key={f.id}
                finding={f}
                stepIdx={f.step_id != null ? stepIdToIdx.get(f.step_id) : undefined}
                onJumpToStep={onJumpToStep}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function FindingCard({
  finding,
  stepIdx,
  onJumpToStep,
}: {
  finding: Finding;
  stepIdx: number | undefined;
  onJumpToStep?: (idx: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const s = severityStyle(finding.severity);
  const hasEvidence =
    finding.evidence && Object.keys(finding.evidence).length > 0;

  return (
    <div className={cn("rounded-md border", s.border, s.bg)}>
      <div className="flex items-start gap-3 px-3.5 py-3">
        <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", s.dot)} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-fg text-[13px] font-medium">{finding.title}</span>
            <span
              className={cn(
                "inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider border",
                s.text,
                s.border,
              )}
            >
              {finding.severity}
            </span>
            <span className="mono text-[10.5px] text-fg-subtle">
              score {finding.score.toFixed(2)}
            </span>
            {stepIdx != null && (
              <button
                onClick={() => onJumpToStep?.(stepIdx)}
                className="mono text-[10.5px] text-accent hover:underline"
                title="Jump to this step in the timeline"
              >
                step #{stepIdx.toString().padStart(3, "0")}
              </button>
            )}
          </div>
          {finding.detail && (
            <p className="text-fg-muted text-[12.5px] leading-relaxed mt-1 break-words">
              {finding.detail}
            </p>
          )}
          {hasEvidence && (
            <>
              <button
                onClick={() => setOpen((o) => !o)}
                className="mt-2 inline-flex items-center gap-1 text-[11px] text-fg-subtle hover:text-fg transition-colors"
              >
                {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                Evidence
              </button>
              {open && (
                <div className="mt-2 rounded-md border border-border bg-bg-subtle p-3 overflow-auto max-h-72">
                  <JsonView value={finding.evidence} />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
