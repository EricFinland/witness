import { useEffect, useState } from "react";
import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  Loader2,
  XCircle,
  AlertTriangle,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Step } from "@/lib/types";
import {
  actionLabel,
  cn,
  formatCost,
  formatLatency,
  formatTokens,
} from "@/lib/utils";
import { Timeline } from "@/components/timeline";
import { ScreenshotDiff } from "@/components/screenshot-diff";
import { DomDiff } from "@/components/dom-diff";
import { LLMCallsPanel } from "@/components/llm-calls-panel";
import { JsonView } from "@/components/json-view";

export const Route = createFileRoute("/traces/$traceId")({
  component: TraceDetail,
});

type Tab = "screenshots" | "dom" | "action" | "llm";

function TraceDetail() {
  const { traceId } = Route.useParams();
  const { data, isLoading, error } = useQuery({
    queryKey: ["trace", traceId],
    queryFn: () => api.getTrace(traceId),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });

  const [selectedIdx, setSelectedIdx] = useState(0);
  const [tab, setTab] = useState<Tab>("screenshots");

  const selected: Step | undefined = data?.steps[selectedIdx];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!data || !data.steps.length) return;
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "j") setSelectedIdx((i) => Math.min(data.steps.length - 1, i + 1));
      if (e.key === "k") setSelectedIdx((i) => Math.max(0, i - 1));
      if (e.key === "1") setTab("screenshots");
      if (e.key === "2") setTab("dom");
      if (e.key === "3") setTab("action");
      if (e.key === "4") setTab("llm");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [data]);

  if (isLoading) return <div className="p-8 text-fg-muted text-sm">Loading…</div>;
  if (error)
    return (
      <div className="p-8 text-danger text-sm">Failed to load: {(error as Error).message}</div>
    );
  if (!data) return null;

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Top bar */}
      <div className="border-b border-border bg-bg shrink-0">
        <div className="mx-auto max-w-[1600px] px-5 py-2.5 flex items-center gap-4">
          <Link
            to="/"
            className="text-fg-muted hover:text-fg flex items-center gap-1.5 text-xs"
          >
            <ArrowLeft size={14} /> All traces
          </Link>
          <span className="text-border">|</span>
          <StatusBadge status={data.status} />
          <span className="mono text-fg-subtle text-xs">{data.id}</span>
          <div className="flex-1 min-w-0">
            <p className="text-fg text-[13px] truncate" title={data.task}>
              {data.task}
            </p>
          </div>
        </div>
        <div className="mx-auto max-w-[1600px] px-5 pb-2.5 flex items-center gap-6 text-xs">
          <Metric label="Model" value={<span className="mono">{data.model ?? "—"}</span>} />
          <Metric label="Steps" value={<span className="mono">{data.step_count}</span>} />
          <Metric
            label="Duration"
            value={<span className="mono">{formatLatency(data.total_latency_ms)}</span>}
          />
          <Metric
            label="Cost"
            value={<span className="mono text-accent">{formatCost(data.total_cost_usd)}</span>}
          />
          <Metric
            label="Tokens"
            value={<span className="mono">{formatTokens(data.total_tokens)}</span>}
          />
        </div>
      </div>

      {/* Two-pane layout */}
      <div className="flex-1 flex min-h-0">
        <Timeline
          steps={data.steps}
          selectedIdx={selectedIdx}
          onSelect={setSelectedIdx}
        />

        <div className="flex-1 flex flex-col min-w-0">
          {selected ? (
            <>
              <StepHeader step={selected} />
              <TabBar tab={tab} setTab={setTab} llmCount={selected.llm_calls.length} />
              <div className="flex-1 min-h-0 overflow-auto bg-bg-muted/30">
                <div className="mx-auto max-w-[1400px]">
                  {tab === "screenshots" && (
                    <ScreenshotDiff traceId={data.id} step={selected} />
                  )}
                  {tab === "dom" && <DomDiff traceId={data.id} step={selected} />}
                  {tab === "action" && <ActionPanel step={selected} />}
                  {tab === "llm" && <LLMCallsPanel step={selected} />}
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex items-center justify-center text-fg-muted text-sm">
              No steps yet.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <span className="text-fg-subtle text-[10.5px] uppercase tracking-wider">{label}</span>
      <span className="text-fg">{value}</span>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map = {
    success: { c: "text-success", Icon: CheckCircle2 },
    error: { c: "text-danger", Icon: XCircle },
    running: { c: "text-warning", Icon: Loader2 },
  } as const;
  const entry = map[status as keyof typeof map] ?? map.running;
  return (
    <span className={cn("flex items-center gap-1 text-xs font-medium", entry.c)}>
      <entry.Icon size={13} className={status === "running" ? "animate-spin" : ""} />
      {status}
    </span>
  );
}

function StepHeader({ step }: { step: Step }) {
  return (
    <div className="border-b border-border bg-bg shrink-0">
      <div className="px-5 py-2.5 flex items-center gap-3 text-xs">
        <span className="mono text-fg-subtle">#{step.idx.toString().padStart(3, "0")}</span>
        <span className="text-fg-muted">·</span>
        <span className="text-fg font-medium">{actionLabel(step.action_type)}</span>
        <span className="text-fg-muted">·</span>
        <span className="mono text-fg-muted">{formatLatency(step.latency_ms)}</span>
        {step.url && (
          <>
            <span className="text-fg-muted">·</span>
            <span className="mono text-fg-subtle truncate min-w-0" title={step.url}>
              {step.url}
            </span>
          </>
        )}
      </div>
      {step.error && (
        <div className="border-t border-danger/20 bg-danger/5 px-5 py-2 flex items-start gap-2">
          <AlertTriangle size={13} className="text-danger shrink-0 mt-[3px]" />
          <span className="mono text-[12px] text-danger/90 leading-relaxed break-words min-w-0">
            {step.error}
          </span>
        </div>
      )}
    </div>
  );
}

function TabBar({
  tab,
  setTab,
  llmCount,
}: {
  tab: Tab;
  setTab: (t: Tab) => void;
  llmCount: number;
}) {
  const tabs: { id: Tab; label: string; hint: string; badge?: number }[] = [
    { id: "screenshots", label: "Screenshots", hint: "1" },
    { id: "dom", label: "DOM Diff", hint: "2" },
    { id: "action", label: "Action", hint: "3" },
    { id: "llm", label: "LLM Calls", hint: "4", badge: llmCount || undefined },
  ];
  return (
    <div className="border-b border-border bg-bg flex items-center px-5 gap-1 shrink-0">
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => setTab(t.id)}
          className={cn(
            "h-9 px-3 text-xs font-medium border-b-2 -mb-px transition-colors flex items-center gap-1.5",
            "focus:outline-none focus-visible:text-fg",
            tab === t.id
              ? "text-fg border-accent"
              : "text-fg-muted hover:text-fg border-transparent",
          )}
        >
          {t.label}
          {t.badge !== undefined && (
            <span className="mono text-[10px] text-fg-subtle">{t.badge}</span>
          )}
          <span className="kbd ml-0.5">{t.hint}</span>
        </button>
      ))}
    </div>
  );
}

function ActionPanel({ step }: { step: Step }) {
  return (
    <div className="p-5">
      <div className="rounded-md border border-border bg-bg-subtle p-4">
        <JsonView value={{ type: step.action_type, ...step.action_payload }} />
      </div>
    </div>
  );
}
