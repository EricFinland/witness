export interface TraceSummary {
  id: string;
  task: string;
  model: string | null;
  status: "running" | "success" | "error";
  started_at: string;
  ended_at: string | null;
  total_cost_usd: number;
  total_tokens: number;
  total_latency_ms: number;
  step_count: number;
}

export interface LLMCall {
  id: number;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  latency_ms: number;
  prompt: string;
  response: string;
  ts: string;
}

export interface Step {
  id: number;
  idx: number;
  action_type: string;
  action_payload: Record<string, unknown>;
  ts: string;
  latency_ms: number;
  error: string | null;
  url: string | null;
  dom_before_path: string | null;
  dom_after_path: string | null;
  shot_before_path: string | null;
  shot_after_path: string | null;
  llm_calls: LLMCall[];
}

export interface Finding {
  id: number;
  trace_id: string;
  step_id: number | null;
  kind: string;
  severity: "info" | "low" | "medium" | "high" | "critical";
  score: number;
  title: string;
  detail: string;
  evidence: Record<string, unknown>;
  created_at: string;
}

export interface TraceDetail extends TraceSummary {
  error: string | null;
  steps: Step[];
  findings?: Finding[];
}

export interface ModelStat {
  model: string;
  trace_count: number;
  total_cost_usd: number;
  total_tokens: number;
  avg_cost_usd: number;
}

export interface DayStat {
  date: string;
  trace_count: number;
  total_cost_usd: number;
  total_tokens: number;
}

export interface Stats {
  totals: {
    trace_count: number;
    success_count: number;
    error_count: number;
    total_cost_usd: number;
    total_tokens: number;
    avg_cost_usd: number;
    avg_tokens: number;
  };
  by_model: ModelStat[];
  by_day: DayStat[];
}
