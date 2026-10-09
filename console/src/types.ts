export type Status = "done" | "awaiting_approval" | "stopped";

export interface Ticket {
  ticket_id: string;
  channel: string;
  customer_id: string | null;
  preview: string;
  status: Status;
  intent: string | null;
  decision: string | null;
  terminal_reason: string | null;
  cost_inr: number;
  latency_ms: number | null;
  source: string;
  created_at: string;
  updated_at: string;
}

export interface Approval {
  approval_id: string;
  ticket_id: string;
  tool_name: string;
  tool_args: Record<string, unknown>;
  reason: string | null;
  status: string;
  approver_id: string | null;
  note: string | null;
  requested_at: string;
  decided_at: string | null;
}

export type RungResult =
  | "passed"
  | "blocked"
  | "not_reached"
  | "not_needed"
  | "waits_for_a_person"
  | "approved_by_a_person";

export interface Rung {
  check: string;
  result: RungResult;
}

export interface Step {
  node: string;
  at: string | null;
  took_ms: number | null;
  detail: Record<string, any>;
}

export interface AuditRow {
  tool_name: string;
  tool_args: Record<string, unknown>;
  result: unknown;
  error: string | null;
  authorized_by: "policy" | "human" | "denied";
  approver_id: string | null;
  check_name: string | null;
  latency_ms: number;
  at: string;
}

export interface Run {
  ticket: Ticket;
  message: string;
  steps: Step[];
  audit: AuditRow[];
  approval: Approval | null;
  reply: string | null;
  trace_id: string | null;
  trace_url: string | null;
  note: string | null;
}

export interface Sample {
  label: string;
  raw_message: string;
  customer_id: string | null;
  channel: string;
}

export interface Config {
  demo: boolean;
  token_required: boolean;
  models: { agent: string; classifier: string; checker: string };
  samples: Sample[];
  checks: string[];
  auto_approve_refund_inr: number;
}

export interface DemoStatus {
  loading: boolean;
  loaded: number;
  total: number;
  error: string | null;
  reset_in_s: number;
  spent_inr: number;
  budget_inr: number | null;
}

export interface Version {
  version: string;
  change: string;
  model: string;
  cases: string;
  date: string;
  metrics: Record<string, number | null>;
}

export interface Target {
  metric: string;
  label: string;
  target: string;
  gates: string;
  value: number | null;
  shown: string;
  met: boolean | null;
  ci: string;
}

export interface Metrics {
  versions: Version[];
  targets: Target[];
  latest: string | null;
  comparison: Record<string, string>[];
}
