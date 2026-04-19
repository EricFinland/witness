import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatCost, formatLatency, formatRelative, formatTokens } from "@/lib/utils";
import { CheckCircle2, Circle, XCircle, Loader2 } from "lucide-react";

export const Route = createFileRoute("/")({
  component: TraceList,
});

function StatusPill({ status }: { status: string }) {
  if (status === "success")
    return (
      <span className="inline-flex items-center gap-1 text-success text-xs font-medium">
        <CheckCircle2 size={12} strokeWidth={2.5} /> success
      </span>
    );
  if (status === "error")
    return (
      <span className="inline-flex items-center gap-1 text-danger text-xs font-medium">
        <XCircle size={12} strokeWidth={2.5} /> error
      </span>
    );
  if (status === "running")
    return (
      <span className="inline-flex items-center gap-1 text-warning text-xs font-medium">
        <Loader2 size={12} strokeWidth={2.5} className="animate-spin" /> running
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-fg-muted text-xs">
      <Circle size={12} /> {status}
    </span>
  );
}

function TraceList() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["traces"],
    queryFn: api.listTraces,
    refetchInterval: 5000,
  });

  return (
    <div className="max-w-[1400px] mx-auto px-6 py-8">
      <div className="flex items-baseline justify-between mb-6">
        <div>
          <h1 className="text-[22px] font-semibold tracking-tight">Traces</h1>
          <p className="text-fg-muted text-xs mt-1">
            Every run of an instrumented agent. Newest first.
          </p>
        </div>
        <div className="text-xs text-fg-subtle font-mono">
          {data ? `${data.length} trace${data.length === 1 ? "" : "s"}` : ""}
        </div>
      </div>

      {isLoading && <Skeletons />}
      {error && (
        <div className="rounded-md border border-danger/30 bg-danger/5 text-danger text-sm p-4">
          Failed to load traces: {(error as Error).message}
        </div>
      )}

      {data && data.length === 0 && (
        <div className="rounded-md border border-border border-dashed p-12 text-center">
          <p className="text-fg text-sm font-medium">No traces yet.</p>
          <p className="text-fg-muted text-xs mt-2">
            Wrap a Browser Use agent with{" "}
            <code className="mono text-accent">witness.instrument(agent)</code> and run it.
          </p>
        </div>
      )}

      {data && data.length > 0 && (
        <div className="rounded-lg border border-border overflow-hidden bg-bg-card">
          <table className="w-full text-sm">
            <thead className="bg-bg-muted/50 border-b border-border">
              <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                <th className="px-4 py-2.5 font-medium">Task</th>
                <th className="px-4 py-2.5 font-medium w-24">Status</th>
                <th className="px-4 py-2.5 font-medium w-20 text-right">Steps</th>
                <th className="px-4 py-2.5 font-medium w-24 text-right">Duration</th>
                <th className="px-4 py-2.5 font-medium w-24 text-right">Cost</th>
                <th className="px-4 py-2.5 font-medium w-20 text-right">Tokens</th>
                <th className="px-4 py-2.5 font-medium w-40">Model</th>
                <th className="px-4 py-2.5 font-medium w-28 text-right">Started</th>
              </tr>
            </thead>
            <tbody>
              {data.map((t) => (
                <tr
                  key={t.id}
                  className="border-t border-border hover:bg-bg-muted/60 transition-colors"
                >
                  <td className="px-4 py-3">
                    <Link
                      to="/traces/$traceId"
                      params={{ traceId: t.id }}
                      className="flex flex-col gap-0.5 group"
                    >
                      <span className="text-fg group-hover:text-accent line-clamp-1">
                        {t.task}
                      </span>
                      <span className="mono text-fg-subtle text-[11px]">{t.id}</span>
                    </Link>
                  </td>
                  <td className="px-4 py-3">
                    <StatusPill status={t.status} />
                  </td>
                  <td className="px-4 py-3 text-right mono text-fg-muted">
                    {t.step_count}
                  </td>
                  <td className="px-4 py-3 text-right mono text-fg-muted">
                    {formatLatency(t.total_latency_ms)}
                  </td>
                  <td className="px-4 py-3 text-right mono text-fg">
                    {formatCost(t.total_cost_usd)}
                  </td>
                  <td className="px-4 py-3 text-right mono text-fg-muted">
                    {formatTokens(t.total_tokens)}
                  </td>
                  <td className="px-4 py-3 mono text-fg-muted text-xs truncate">
                    {t.model ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right text-fg-muted text-xs">
                    {formatRelative(t.started_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function Skeletons() {
  return (
    <div className="rounded-lg border border-border overflow-hidden bg-bg-card">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className="h-14 border-t first:border-t-0 border-border animate-pulse bg-gradient-to-r from-bg-card via-bg-muted to-bg-card"
        />
      ))}
    </div>
  );
}
