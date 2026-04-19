import { useMemo } from "react";

/**
 * Minimal JSON syntax highlighter — no external dep, Tailwind-theme colors.
 * Renders the stringified JSON with classes on keys/strings/numbers/bools.
 */
export function JsonView({ value }: { value: unknown }) {
  const pretty = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const parts = useMemo(() => tokenize(pretty), [pretty]);

  return (
    <pre className="mono text-[12.5px] leading-[1.6] text-fg m-0 whitespace-pre-wrap break-words">
      {parts.map((p, i) => (
        <span key={i} className={p.className}>
          {p.text}
        </span>
      ))}
    </pre>
  );
}

interface Tok {
  text: string;
  className: string;
}

const PATTERN =
  /("(?:\\u[0-9a-fA-F]{4}|\\.|[^"\\])*"(?:\s*:)?)|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|([{}[\],])|(\s+)/g;

function tokenize(input: string): Tok[] {
  const out: Tok[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = PATTERN.exec(input)) !== null) {
    if (m.index > last) {
      out.push({ text: input.slice(last, m.index), className: "text-fg" });
    }
    const [full, str, kw, num, punct, ws] = m;
    if (str !== undefined) {
      const isKey = str.endsWith(":");
      if (isKey) {
        out.push({ text: str.slice(0, -1), className: "text-accent" });
        out.push({ text: ":", className: "text-fg-subtle" });
      } else {
        out.push({ text: str, className: "text-success" });
      }
    } else if (kw !== undefined) {
      out.push({ text: kw, className: kw === "null" ? "text-fg-subtle" : "text-warning" });
    } else if (num !== undefined) {
      out.push({ text: num, className: "text-warning" });
    } else if (punct !== undefined) {
      out.push({ text: punct, className: "text-fg-subtle" });
    } else if (ws !== undefined) {
      out.push({ text: ws, className: "" });
    } else {
      out.push({ text: full, className: "text-fg" });
    }
    last = m.index + full.length;
  }
  if (last < input.length) {
    out.push({ text: input.slice(last), className: "text-fg" });
  }
  return out;
}
