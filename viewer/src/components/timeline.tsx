import type { Step } from "@/lib/types";
import { actionLabel, cn, formatLatency } from "@/lib/utils";
import { CircleDot, AlertCircle } from "lucide-react";

interface Props {
  steps: Step[];
  selectedIdx: number;
  onSelect: (idx: number) => void;
}

export function Timeline({ steps, selectedIdx, onSelect }: Props) {
  const maxLatency = Math.max(1, ...steps.map((s) => s.latency_ms));

  return (
    <div className="w-[320px] shrink-0 border-r border-border bg-bg overflow-y-auto">
      <div className="sticky top-0 bg-bg border-b border-border px-5 py-2.5 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider text-fg-subtle">
          Timeline
        </span>
        <span className="text-[10px] text-fg-subtle mono flex items-center gap-1">
          <span className="kbd">j</span>
          <span className="kbd">k</span>
        </span>
      </div>
      <ul>
        {steps.map((s) => {
          // True-proportional scaling: no artificial floor. A 100ms step next
          // to a 3000ms step should look 30x narrower. Use a 1px minimum via
          // CSS so zero-latency steps don't completely vanish.
          const pct = Math.max(1, (s.latency_ms / maxLatency) * 100);
          const active = s.idx === selectedIdx;
          return (
            <li key={s.id}>
              <button
                onClick={() => onSelect(s.idx)}
                className={cn(
                  "w-full text-left px-5 py-2.5 flex flex-col gap-2 border-l-2 transition-colors",
                  active
                    ? "bg-bg-muted border-accent"
                    : "border-transparent hover:bg-bg-muted/60",
                )}
              >
                <div className="flex items-center gap-2">
                  <span className="mono text-[11px] text-fg-subtle w-7 shrink-0">
                    {s.idx.toString().padStart(3, "0")}
                  </span>
                  {s.error ? (
                    <AlertCircle size={11} className="text-danger shrink-0" />
                  ) : (
                    <CircleDot size={11} className={cn(active ? "text-accent" : "text-fg-subtle", "shrink-0")} />
                  )}
                  <span
                    className={cn(
                      "text-[12.5px] truncate flex-1",
                      active ? "text-fg" : "text-fg-muted",
                    )}
                  >
                    {actionLabel(s.action_type)}
                  </span>
                  <span className="mono text-[10.5px] text-fg-subtle shrink-0">
                    {formatLatency(s.latency_ms)}
                  </span>
                </div>
                <div className="relative h-[3px] bg-border rounded-full overflow-hidden ml-9">
                  <div
                    className={cn(
                      "h-full rounded-full transition-[width]",
                      s.error ? "bg-danger" : active ? "bg-accent" : "bg-accent/40",
                    )}
                    style={{ width: `${pct}%`, minWidth: "1px" }}
                  />
                </div>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
