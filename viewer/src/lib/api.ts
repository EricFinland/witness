import type { TraceSummary, TraceDetail } from "./types";

const BASE = "";

async function j<T>(url: string): Promise<T> {
  const r = await fetch(BASE + url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export const api = {
  listTraces: () => j<TraceSummary[]>("/api/traces"),
  getTrace: (id: string) => j<TraceDetail>(`/api/traces/${id}`),
  blobUrl: (traceId: string, path: string) =>
    `/api/traces/${traceId}/blobs/${path}`,
  async getBlobText(traceId: string, path: string): Promise<string> {
    const r = await fetch(BASE + `/api/traces/${traceId}/blobs/${path}`);
    if (!r.ok) throw new Error(`${r.status}`);
    return r.text();
  },
};
