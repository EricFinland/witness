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

export interface TraceDetail extends TraceSummary {
  error: string | null;
  steps: Step[];
}
