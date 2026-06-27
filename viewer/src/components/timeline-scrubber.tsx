import type { Step } from "@/lib/types";
import { cn } from "@/lib/utils";

interface Props {
  steps: Step[];
  selectedIdx: number;
  onSelect: (idx: number) => void;
  // Step ids that carry an injection/exfil finding, to tint their ticks.
  warnSteps?: Set<number>;
}

// Compact horizontal step slider so the user can jump across steps quickly.
// Each step is a clickable tick; the selected one is highlighted. Error steps
// render danger-colored, warning steps amber.
export function TimelineScrubber({
  steps,
  selectedIdx,
  onSelect,
  warnSteps,
}: Props) {
  if (steps.length === 0) return null;

  return (
    <div className="border-b border-border bg-bg shrink-0 px-5 py-2">
      <div className="flex items-center gap-3">
        <span className="text-[10px] uppercase tracking-wider text-fg-subtle shrink-0">
          Steps
        </span>
        <div className="flex-1 flex items-center gap-[3px] overflow-x-auto py-1">
          {steps.map((s) => {
            const active = s.idx === selectedIdx;
            const warn = warnSteps?.has(s.id);
            return (
              <button
                key={s.id}
                onClick={() => onSelect(s.idx)}
                title={`#${s.idx.toString().padStart(3, "0")} ${s.action_type}`}
                aria-label={`Step ${s.idx}`}
                className={cn(
                  "h-6 min-w-[7px] flex-1 max-w-[28px] rounded-sm transition-colors",
                  active
                    ? "bg-accent"
                    : s.error
                      ? "bg-danger/60 hover:bg-danger"
                      : warn
                        ? "bg-amber-400/60 hover:bg-amber-400"
                        : "bg-border-muted hover:bg-fg-subtle",
                )}
              />
            );
          })}
        </div>
        <span className="mono text-[10.5px] text-fg-subtle shrink-0 w-16 text-right">
          {(selectedIdx + 1).toString()} / {steps.length}
        </span>
      </div>
    </div>
  );
}
