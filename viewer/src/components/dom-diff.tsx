import { useEffect, useState, useMemo } from "react";
import { diffLines } from "diff";
import type { Step } from "@/lib/types";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

interface Props {
  traceId: string;
  step: Step;
}

/**
 * A row that shows up in exactly one column, or in both unchanged.
 * `beforeLine` / `afterLine` are 1-indexed; null means the row is blank on
 * that side (e.g. a pure insertion has beforeLine=null).
 */
interface Row {
  kind: "equal" | "insert" | "delete";
  beforeLine: number | null;
  afterLine: number | null;
  text: string;
}

export function DomDiff({ traceId, step }: Props) {
  const [before, setBefore] = useState<string | null>(null);
  const [after, setAfter] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setErr(null);
      try {
        const [b, a] = await Promise.all([
          step.dom_before_path ? api.getBlobText(traceId, step.dom_before_path) : Promise.resolve(""),
          step.dom_after_path ? api.getBlobText(traceId, step.dom_after_path) : Promise.resolve(""),
        ]);
        if (cancelled) return;
        setBefore(b);
        setAfter(a);
      } catch (e) {
        if (!cancelled) setErr(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [traceId, step.id]);

  const rows = useMemo(() => buildAlignedRows(before ?? "", after ?? ""), [before, after]);

  const hasChange = rows.some((r) => r.kind !== "equal");
  const identical = !!(before && after) && before === after;

  if (loading) return <SkeletonBody />;
  if (err) return <div className="p-8 text-danger text-xs">Failed to load DOM: {err}</div>;
  if (!before && !after)
    return <EmptyState title="No DOM captured" sub="This step didn't record before/after HTML." />;
  if (identical || !hasChange)
    return <EmptyState title="No DOM change in this step" sub="The before and after HTML are identical." />;

  return (
    <div className="p-5">
      <div className="rounded-md border border-border overflow-hidden bg-bg-subtle">
        <div className="grid grid-cols-2 border-b border-border bg-bg-muted/50">
          <div className="px-4 py-2 text-[10px] uppercase tracking-wider text-fg-subtle border-r border-border">
            Before
          </div>
          <div className="px-4 py-2 text-[10px] uppercase tracking-wider text-fg-subtle">
            After
          </div>
        </div>
        <div className="grid grid-cols-2 max-h-[78vh] overflow-auto">
          <SideColumn side="before" rows={rows} />
          <SideColumn side="after" rows={rows} />
        </div>
      </div>
    </div>
  );
}

function SideColumn({ side, rows }: { side: "before" | "after"; rows: Row[] }) {
  return (
    <div className={cn(side === "before" && "border-r border-border")}>
      <pre className="mono text-[12px] leading-[1.55] m-0 p-0">
        {rows.map((r, i) => {
          const show = showsOn(r, side);
          const lineNum = side === "before" ? r.beforeLine : r.afterLine;
          const kind = show ? (side === "before" && r.kind === "delete" ? "delete"
            : side === "after" && r.kind === "insert" ? "insert"
            : "equal") : "blank";

          return (
            <div
              key={i}
              className={cn(
                "flex min-h-[20px] border-l-2",
                kind === "insert" && "bg-success/10 border-success/70",
                kind === "delete" && "bg-danger/10 border-danger/70",
                kind === "equal" && "border-transparent",
                kind === "blank" && "border-transparent bg-bg-muted/30",
              )}
            >
              <span
                className={cn(
                  "select-none shrink-0 w-10 px-2 text-right text-fg-subtle",
                  "tabular-nums text-[11px] pt-[1px]",
                )}
              >
                {lineNum ?? ""}
              </span>
              <span
                className={cn(
                  "flex-1 px-2 whitespace-pre-wrap break-words",
                  kind === "insert" && "text-success",
                  kind === "delete" && "text-danger",
                  kind === "equal" && "text-fg-muted",
                  kind === "blank" && "",
                )}
              >
                {show ? r.text || " " : " "}
              </span>
            </div>
          );
        })}
      </pre>
    </div>
  );
}

function showsOn(r: Row, side: "before" | "after"): boolean {
  if (r.kind === "equal") return true;
  if (r.kind === "insert") return side === "after";
  return side === "before";
}

/**
 * Walk the diffLines() output and emit aligned Rows with separate line numbers
 * for each side. Adjacent delete+insert blocks sit next to each other; pure
 * insertions/deletions have a blank row on the other side.
 */
function buildAlignedRows(before: string, after: string): Row[] {
  const parts = diffLines(before, after);
  const rows: Row[] = [];
  let bLine = 0;
  let aLine = 0;

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const lines = splitLines(part.value);

    if (!part.added && !part.removed) {
      // equal block
      for (const l of lines) {
        bLine++;
        aLine++;
        rows.push({ kind: "equal", beforeLine: bLine, afterLine: aLine, text: l });
      }
      continue;
    }

    if (part.removed) {
      // Pair with the following insert (if any) for a symmetric modify block.
      const next = parts[i + 1];
      const paired = next && next.added;
      const removedLines = lines;
      const addedLines = paired ? splitLines(next.value) : [];
      const pairs = Math.max(removedLines.length, addedLines.length);
      for (let k = 0; k < pairs; k++) {
        const rem = removedLines[k];
        const add = addedLines[k];
        if (rem !== undefined && add !== undefined) {
          bLine++;
          aLine++;
          rows.push({ kind: "delete", beforeLine: bLine, afterLine: null, text: rem });
          rows.push({ kind: "insert", beforeLine: null, afterLine: aLine, text: add });
        } else if (rem !== undefined) {
          bLine++;
          rows.push({ kind: "delete", beforeLine: bLine, afterLine: null, text: rem });
        } else if (add !== undefined) {
          aLine++;
          rows.push({ kind: "insert", beforeLine: null, afterLine: aLine, text: add });
        }
      }
      if (paired) i++;
      continue;
    }

    // pure insert (not preceded by a removed block)
    for (const l of lines) {
      aLine++;
      rows.push({ kind: "insert", beforeLine: null, afterLine: aLine, text: l });
    }
  }
  return rows;
}

function splitLines(s: string): string[] {
  if (s === "") return [];
  const arr = s.split("\n");
  // diffLines keeps trailing newlines inside a block; drop the empty tail.
  if (arr[arr.length - 1] === "") arr.pop();
  return arr;
}

function EmptyState({ title, sub }: { title: string; sub?: string }) {
  return (
    <div className="h-full flex flex-col items-center justify-center py-20 text-center">
      <div className="text-fg text-sm font-medium">{title}</div>
      {sub && <div className="text-fg-muted text-xs mt-1.5">{sub}</div>}
    </div>
  );
}

function SkeletonBody() {
  return (
    <div className="p-5">
      <div className="rounded-md border border-border overflow-hidden">
        <div className="grid grid-cols-2">
          {Array.from({ length: 16 }).map((_, i) => (
            <div
              key={i}
              className="h-5 border-t border-border animate-pulse bg-gradient-to-r from-bg-subtle via-bg-muted to-bg-subtle"
            />
          ))}
        </div>
      </div>
    </div>
  );
}
