import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { formatCost, formatTokens } from "@/lib/utils";
import type { ModelStat, DayStat } from "@/lib/types";

export const Route = createFileRoute("/stats")({
  component: StatsPage,
});

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.max(2, Math.round((value / max) * 100)) : 2;
  return (
    <div className="flex items-center gap-2">
      <div
        className={`h-2 rounded-sm ${color}`}
        style={{ width: `${pct}%`, minWidth: "4px", maxWidth: "100%" }}
      />
    </div>
  );
}

export default function StatsPage() {
  const { data, isLoading, error } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api.getStats(30),
  });

  if (isLoading) return <div className="p-8 text-sm text-fg-muted">Loading…</div>;
  if (error || !data)
    return (
      <div className="p-8 text-sm text-danger">Failed to load stats.</div>
    );

  const { totals, by_model, by_day } = data;
  const maxModelCost = by_model[0]?.total_cost_usd ?? 1;
  const maxDayCost = Math.max(...by_day.map((d) => d.total_cost_usd), 1);

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-10">
      {/* Totals */}
      <section>
        <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider mb-4">
          Total
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
          {[
            { label: "Traces", value: totals.trace_count.toLocaleString() },
            { label: "Total cost", value: formatCost(totals.total_cost_usd) },
            { label: "Total tokens", value: formatTokens(totals.total_tokens) },
            { label: "Avg cost / run", value: formatCost(totals.avg_cost_usd) },
            { label: "Avg tokens / run", value: formatTokens(totals.avg_tokens) },
            {
              label: "Success rate",
              value:
                totals.trace_count > 0
                  ? `${Math.round((totals.success_count / totals.trace_count) * 100)}%`
                  : "—",
            },
          ].map(({ label, value }) => (
            <div
              key={label}
              className="bg-bg-card border border-border rounded-lg p-4"
            >
              <div className="text-xs text-fg-muted mb-1">{label}</div>
              <div className="text-lg mono font-semibold text-fg">{value}</div>
            </div>
          ))}
        </div>
      </section>

      {/* By model */}
      {by_model.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider mb-4">
            By Model
          </h2>
          <div className="rounded-lg border border-border overflow-hidden bg-bg-card">
            <table className="w-full text-sm">
              <thead className="bg-bg-muted/50 border-b border-border">
                <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                  <th className="px-4 py-2.5 font-medium">Model</th>
                  <th className="px-4 py-2.5 font-medium text-right">Runs</th>
                  <th className="px-4 py-2.5 font-medium text-right">Cost</th>
                  <th className="px-4 py-2.5 w-24"></th>
                  <th className="px-4 py-2.5 font-medium text-right">Tokens</th>
                  <th className="px-4 py-2.5 font-medium text-right">Avg cost</th>
                </tr>
              </thead>
              <tbody>
                {by_model.map((m: ModelStat) => (
                  <tr
                    key={m.model}
                    className="border-t border-border hover:bg-bg-muted/60 transition-colors"
                  >
                    <td className="px-4 py-3 mono text-fg text-xs truncate">{m.model}</td>
                    <td className="px-4 py-3 text-right mono text-fg-muted">
                      {m.trace_count}
                    </td>
                    <td className="px-4 py-3 text-right mono text-fg">
                      {formatCost(m.total_cost_usd)}
                    </td>
                    <td className="px-4 py-3">
                      <Bar
                        value={m.total_cost_usd}
                        max={maxModelCost}
                        color="bg-success"
                      />
                    </td>
                    <td className="px-4 py-3 text-right mono text-fg-muted">
                      {formatTokens(m.total_tokens)}
                    </td>
                    <td className="px-4 py-3 text-right mono text-fg-muted">
                      {formatCost(m.avg_cost_usd)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* By day */}
      {by_day.length > 0 && (
        <section>
          <h2 className="text-xs font-semibold text-fg-subtle uppercase tracking-wider mb-4">
            By Day{" "}
            <span className="text-fg-subtle font-normal normal-case tracking-normal">
              (last 30 days)
            </span>
          </h2>
          <div className="rounded-lg border border-border overflow-hidden bg-bg-card">
            <table className="w-full text-sm">
              <thead className="bg-bg-muted/50 border-b border-border">
                <tr className="text-left text-[11px] uppercase tracking-wider text-fg-subtle">
                  <th className="px-4 py-2.5 font-medium">Date</th>
                  <th className="px-4 py-2.5 font-medium text-right">Runs</th>
                  <th className="px-4 py-2.5 font-medium text-right">Cost</th>
                  <th className="px-4 py-2.5 w-24"></th>
                  <th className="px-4 py-2.5 font-medium text-right">Tokens</th>
                </tr>
              </thead>
              <tbody>
                {by_day.map((d: DayStat) => (
                  <tr
                    key={d.date}
                    className="border-t border-border hover:bg-bg-muted/60 transition-colors"
                  >
                    <td className="px-4 py-3 mono text-fg-muted text-xs">{d.date}</td>
                    <td className="px-4 py-3 text-right mono text-fg-muted">
                      {d.trace_count}
                    </td>
                    <td className="px-4 py-3 text-right mono text-fg">
                      {formatCost(d.total_cost_usd)}
                    </td>
                    <td className="px-4 py-3">
                      <Bar
                        value={d.total_cost_usd}
                        max={maxDayCost}
                        color="bg-accent"
                      />
                    </td>
                    <td className="px-4 py-3 text-right mono text-fg-muted">
                      {formatTokens(d.total_tokens)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {totals.trace_count === 0 && (
        <p className="text-sm text-fg-muted">
          No traces yet. Run an instrumented agent first.
        </p>
      )}
    </div>
  );
}
