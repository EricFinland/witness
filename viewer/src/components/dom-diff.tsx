import { useEffect, useState, useMemo } from "react";
import { diffLines, Change } from "diff";
import type { Step } from "@/lib/types";
import { api } from "@/lib/api";

interface Props {
  traceId: string;
  step: Step;
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

  const chunks: Change[] = useMemo(() => {
    if (!before && !after) return [];
    return diffLines(before ?? "", after ?? "", { ignoreWhitespace: false });
  }, [before, after]);

  if (loading) return <div className="p-8 text-fg-muted text-xs">Loading DOM…</div>;
  if (err) return <div className="p-8 text-danger text-xs">Failed to load DOM: {err}</div>;
  if (!before && !after)
    return <div className="p-12 text-center text-fg-muted text-sm">No DOM captured.</div>;

  const unchanged = chunks.length === 1 && !chunks[0].added && !chunks[0].removed;
  if (unchanged) {
    return (
      <div className="p-5">
        <div className="text-[11px] uppercase tracking-wider text-fg-subtle mb-2">
          No DOM changes
        </div>
        <pre className="mono text-[12px] p-4 rounded-md bg-bg-subtle border border-border text-fg-muted whitespace-pre-wrap break-words max-h-[70vh] overflow-auto">
          {(before ?? after ?? "").slice(0, 8000)}
        </pre>
      </div>
    );
  }

  return (
    <div className="p-5">
      <div className="rounded-md border border-border overflow-hidden">
        <pre className="mono text-[12px] leading-[1.55] whitespace-pre-wrap break-words max-h-[75vh] overflow-auto bg-bg-subtle">
          {chunks.map((c, i) => {
            if (c.added)
              return (
                <span
                  key={i}
                  className="block bg-success/10 text-success border-l-2 border-success/70 pl-3 pr-2"
                >
                  {prefix("+", c.value)}
                </span>
              );
            if (c.removed)
              return (
                <span
                  key={i}
                  className="block bg-danger/10 text-danger border-l-2 border-danger/70 pl-3 pr-2"
                >
                  {prefix("-", c.value)}
                </span>
              );
            return (
              <span key={i} className="block text-fg-muted pl-3 pr-2">
                {prefix(" ", c.value)}
              </span>
            );
          })}
        </pre>
      </div>
    </div>
  );
}

function prefix(sym: string, s: string): string {
  return s
    .split("\n")
    .map((line, i, arr) => (i === arr.length - 1 && line === "" ? "" : sym + " " + line))
    .filter((l) => l !== "")
    .join("\n") + (s.endsWith("\n") ? "\n" : "");
}
