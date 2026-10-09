import type { Rung, Ticket } from "./types";

export type Tone = "good" | "wait" | "person" | "stop" | "plain";

export function rupees(amount: unknown): string {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "Rs ?";
  const whole = Number.isInteger(value);
  return `Rs ${value.toLocaleString("en-IN", { minimumFractionDigits: whole ? 0 : 2, maximumFractionDigits: 2 })}`;
}

export function cost(amount: number): string {
  if (!amount) return "Rs 0";
  return amount < 0.01 ? "under Rs 0.01" : `Rs ${amount.toFixed(2)}`;
}

export function duration(ms: number | null): string {
  if (ms === null) return "";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes} min ${Math.round((ms % 60_000) / 1000)} s`;
  return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

export function when(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function words(code: string | null | undefined): string {
  return (code ?? "").replace(/_/g, " ");
}

const WHY: Record<string, string> = {
  message_signal: "the message itself is one the escalation policy sends to a person",
  out_of_scope: "it is outside what support covers",
  low_confidence: "the classifier was not sure what was being asked",
  parse_failure: "the model's output could not be read",
  no_classification: "the ticket could not be classified",
  by_plan: "the plan chose to hand it over",
  action_required: "a policy calls for an action no tool can take",
  act_without_tool: "the plan chose to act and named no tool",
  human_denied: "a person denied the action",
  tool_error: "the tool refused the action",
  verify_failed: "the reply failed its check three times",
  action_mismatch: "the reply did not match the action that ran",
  loop_cap: "the plan did not settle",
};

export function outcome(ticket: Pick<Ticket, "status" | "terminal_reason">): { label: string; tone: Tone; why: string } {
  const reason = ticket.terminal_reason ?? "";
  if (ticket.status === "awaiting_approval") return { label: "Waiting for approval", tone: "wait", why: "" };
  if (ticket.status === "stopped") return { label: "Stopped", tone: "stop", why: "the run did not finish" };
  if (reason === "answered") return { label: "Answered", tone: "good", why: "" };
  if (reason === "acted") return { label: "Acted", tone: "good", why: "" };
  if (reason.startsWith("escalated_guardrail_")) {
    const check = reason.replace("escalated_guardrail_", "");
    return { label: "Sent to a person", tone: "stop", why: `the guardrail refused the action. Failed check: ${checkName(check)}` };
  }
  if (reason.startsWith("escalated_")) {
    const key = reason.replace("escalated_", "");
    return { label: "Sent to a person", tone: "person", why: WHY[key] ?? words(key) };
  }
  return { label: "Done", tone: "plain", why: words(reason) };
}

const CHECK_NAMES: Record<string, string> = {
  tool_exists: "the tool exists",
  allowlist: "the tool is allowed for this intent",
  arguments: "the arguments match the tool's schema",
  escalation_rules: "the message is not one for a person",
  order_scope: "it is the customer's own order",
  tool_budget: "under four actions on this ticket",
  cost_cap: "under the cost cap",
  hard_ceiling: "under the Rs 25,000 ceiling",
  policy_rules: "the policy's numeric rules hold",
  duplicate_refund: "no refund on this order in 24 hours",
  approval: "under the auto approve ceiling",
  human_denied: "a person's decision",
};

export function checkName(check: string): string {
  return CHECK_NAMES[check] ?? words(check);
}

export function rungMark(rung: Rung): { mark: string; tone: Tone; says: string } {
  switch (rung.result) {
    case "passed":
      return { mark: "✓", tone: "good", says: "passed" };
    case "blocked":
      return { mark: "✕", tone: "stop", says: "blocked here" };
    case "not_reached":
      return { mark: "·", tone: "plain", says: "not reached" };
    case "waits_for_a_person":
      return { mark: "!", tone: "wait", says: "over the ceiling, waits for a person" };
    case "approved_by_a_person":
      return { mark: "✓", tone: "good", says: "over the ceiling, approved by a person" };
    default:
      return { mark: "✓", tone: "good", says: "no approval needed" };
  }
}

export function modelName(name: string): string {
  return name.split(":").slice(1).join(":") || name;
}
