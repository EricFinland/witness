import { useState } from "react";
import type { Step, LLMCall } from "@/lib/types";
import { cn, formatCost, formatLatency, formatTokens } from "@/lib/utils";
import { ChevronRight, X } from "lucide-react";

export function LLMCallsPanel({ step }: { step: Step }) {
  const [open, setOpen] = useState<LLMCall | null>(null);
  if (step.llm_calls.length === 0)
    return (
      <div className="p-12 text-center text-fg-muted text-sm">
        No LLM calls recorded for this step.
      </div>
    );

  return (
    <div className="p-5">
      <div className="rounded-md border border-border overflow-hidden bg-bg-card">
        <table className="w-full text-sm">
          <thead className="bg-bg-muted/50 border-b border-border">
            <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
              <th className="px-4 py-2 font-medium">Model</th>
              <th className="px-4 py-2 font-medium text-right w-24">Prompt</th>
              <th className="px-4 py-2 font-medium text-right w-24">Completion</th>
              <th className="px-4 py-2 font-medium text-right w-24">Cost</th>
              <th className="px-4 py-2 font-medium text-right w-24">Latency</th>
              <th className="w-8" />
            </tr>
          </thead>
          <tbody>
            {step.llm_calls.map((c) => (
              <tr
                key={c.id}
                onClick={() => setOpen(c)}
                className="border-t border-border hover:bg-bg-muted/60 cursor-pointer"
              >
                <td className="px-4 py-2.5 mono text-[12px] text-fg">{c.model}</td>
                <td className="px-4 py-2.5 text-right mono text-[12px] text-fg-muted">
                  {formatTokens(c.prompt_tokens)}
                </td>
                <td className="px-4 py-2.5 text-right mono text-[12px] text-fg-muted">
                  {formatTokens(c.completion_tokens)}
                </td>
                <td className="px-4 py-2.5 text-right mono text-[12px] text-accent">
                  {formatCost(c.cost_usd)}
                </td>
                <td className="px-4 py-2.5 text-right mono text-[12px] text-fg-muted">
                  {formatLatency(c.latency_ms)}
                </td>
                <td className="px-2 text-fg-subtle">
                  <ChevronRight size={14} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {open && <CallDialog call={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function CallDialog({ call, onClose }: { call: LLMCall; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 z-50 bg-black/70 flex items-center justify-center p-8"
      onClick={onClose}
    >
      <div
        className="relative bg-bg-card border border-border rounded-lg w-full max-w-5xl h-[85vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-3 px-5 py-3 border-b border-border">
          <span className="mono text-sm text-fg">{call.model}</span>
          <span className="text-fg-subtle">·</span>
          <span className="mono text-xs text-fg-muted">
            {formatTokens(call.prompt_tokens)} in · {formatTokens(call.completion_tokens)} out
          </span>
          <span className="text-fg-subtle">·</span>
          <span className="mono text-xs text-accent">{formatCost(call.cost_usd)}</span>
          <span className="text-fg-subtle">·</span>
          <span className="mono text-xs text-fg-muted">{formatLatency(call.latency_ms)}</span>
          <button
            onClick={onClose}
            className="ml-auto text-fg-muted hover:text-fg rounded-md p-1"
          >
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 min-h-0 grid grid-cols-2">
          <Pane label="Prompt" body={call.prompt} />
          <div className="border-l border-border">
            <Pane label="Response" body={call.response} />
          </div>
        </div>
      </div>
    </div>
  );
}

function Pane({ label, body }: { label: string; body: string }) {
  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="px-4 py-2 border-b border-border text-[10px] uppercase tracking-wider text-fg-subtle bg-bg-muted/40">
        {label}
      </div>
      <pre
        className={cn(
          "flex-1 min-h-0 overflow-auto p-4 mono text-[12px] leading-relaxed",
          "whitespace-pre-wrap break-words text-fg",
        )}
      >
        {body || <span className="text-fg-subtle">(empty)</span>}
      </pre>
    </div>
  );
}
