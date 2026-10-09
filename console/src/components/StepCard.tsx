import { checkName, duration, modelName, rupees, words } from "../format";
import type { Tone } from "../format";
import type { Step } from "../types";
import { Field, Json } from "./bits";
import { Ladder } from "./Ladder";

const TITLES: Record<string, string> = {
  redact: "Redact",
  classify: "Classify",
  retrieve: "Retrieve",
  plan: "Plan",
  guardrail: "Guardrail",
  await_approval: "Approval",
  act: "Act",
  draft: "Draft",
  verify: "Verify",
  escalate: "Escalate",
  respond: "Respond",
};

export function summary(step: Step): { text: string; tone: Tone } {
  const d = step.detail;
  switch (step.node) {
    case "redact": {
      const found = (d.placeholders ?? []).length;
      return { text: found ? `${found} personal ${found === 1 ? "detail" : "details"} replaced with placeholders` : "No personal details in the message", tone: "plain" };
    }
    case "classify":
      return { text: `${words(d.intent)}, confidence ${Number(d.confidence).toFixed(2)}${d.order_id ? `, order ${d.order_id}` : ", no order named"}`, tone: "plain" };
    case "retrieve": {
      const count = (d.policies ?? []).length;
      const order = d.order ? (d.order.belongs_to_customer === false ? "the order is on another account" : `order ${d.order.order_id} is ${d.order.status}`) : "no order found";
      return { text: `${count} policy ${count === 1 ? "section" : "sections"}, ${order}`, tone: "plain" };
    }
    case "plan":
      if (d.decision === "act") return { text: `Wants to call ${d.tool_name}`, tone: "plain" };
      if (d.decision === "escalate") return { text: "Chose to hand it to a person", tone: "person" };
      return { text: "Chose to answer", tone: "plain" };
    case "guardrail":
      if (d.outcome === "deny") return { text: `Blocked ${d.tool_name}. Failed check: ${checkName(d.check)}`, tone: "stop" };
      if (d.outcome === "approval") return { text: `${d.tool_name} passed every check and waits for a person`, tone: "wait" };
      return { text: `Allowed ${d.tool_name}${d.authorized_by === "human" ? ", approved by a person" : ""}`, tone: "good" };
    case "await_approval":
      if (d.approved === undefined) return { text: "Waiting for a person", tone: "wait" };
      return d.approved ? { text: `Approved by ${d.approver_id}`, tone: "good" } : { text: `Denied by ${d.approver_id}`, tone: "stop" };
    case "act":
      return d.error ? { text: `${d.name} was refused by the server`, tone: "stop" } : { text: `${d.name} ran`, tone: "good" };
    case "draft":
      return { text: `${String(d.draft ?? "").split(/\s+/).filter(Boolean).length} words`, tone: "plain" };
    case "verify":
      if (d.verdict === "pass") return { text: "The reply passed every check", tone: "good" };
      if (d.verdict === "retry") return { text: "Sent back to be written again", tone: "wait" };
      return { text: "Failed its checks too many times", tone: "stop" };
    case "escalate":
      return { text: d.escalation_id ? `Case ${d.escalation_id} opened in the ${words(d.queue)} queue` : "Handed to a person", tone: "person" };
    case "respond":
      return { text: d.terminal_reason === "acted" || d.terminal_reason === "answered" ? "Reply sent" : "Fixed reply sent, promising nothing", tone: "plain" };
    default:
      return { text: "", tone: "plain" };
  }
}

function Detail({ step }: { step: Step }) {
  const d = step.detail;
  switch (step.node) {
    case "redact":
      return d.placeholders?.length ? <p className="muted">Placeholders: {d.placeholders.join(", ")}. The model and the trace only ever see these.</p> : null;
    case "classify":
      return (
        <dl className="fields">
          <Field label="Urgency">{d.urgency}</Field>
          <Field label="Sentiment">{d.sentiment}</Field>
          <Field label="Classified by">{modelName(d.classified_by ?? "")}{d.fell_back ? ", standing in" : ""}</Field>
          <Field label="Reasoning">{d.reasoning}</Field>
        </dl>
      );
    case "retrieve":
      return (
        <>
          <ul className="chips">
            {(d.policies ?? []).map((p: { doc_id: string; section: string; score: number; pinned?: boolean }, i: number) => (
              <li key={i}>
                <code>{p.doc_id}</code> {p.section}{" "}
                <span className="muted" title={p.pinned ? "This policy overrides the others, so the plan is shown it for every ticket." : "How close the search found this section to the message."}>
                  {p.pinned ? "always shown" : p.score.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
          {d.order && d.order.belongs_to_customer !== false && (
            <dl className="fields">
              <Field label="Order total">{rupees(d.order.total_inr)}</Field>
              <Field label="Still refundable">{rupees(d.order.refundable_inr)}</Field>
              <Field label="Paid by">{d.order.payment_method}</Field>
              {d.order.hours_since_delivered != null && <Field label="Delivered">{d.order.hours_since_delivered} hours ago</Field>}
            </dl>
          )}
          {d.order?.belongs_to_customer === false && <p className="muted">The order's details were withheld from the model.</p>}
        </>
      );
    case "plan":
      return (
        <>
          <p>{d.rationale}</p>
          <dl className="fields">
            <Field label="Cites">{(d.cites ?? []).map((c: string) => <code key={c}>{c} </code>)}</Field>
            {d.escalation_reason && <Field label="Reason">{words(d.escalation_reason)}</Field>}
          </dl>
          {d.tool_args && <Json value={d.tool_args} />}
        </>
      );
    case "guardrail":
      return (
        <>
          {d.detail && <p className={d.outcome === "deny" ? "verdict stop" : "verdict wait"}>{d.detail}</p>}
          <Ladder rungs={d.ladder ?? []} />
        </>
      );
    case "await_approval":
      return d.note ? <p>Note: {d.note}</p> : null;
    case "act":
      return (
        <>
          <dl className="fields">
            <Field label="Authorized by">{d.authorized_by === "human" ? `a person, ${d.approver_id}` : "policy"}</Field>
            <Field label="Server took">{duration(d.latency_ms)}</Field>
          </dl>
          <div className="pair">
            <div>
              <h4>Arguments</h4>
              <Json value={d.args} />
            </div>
            <div>
              <h4>{d.error ? "Refused" : "Result"}</h4>
              {d.error ? <p className="verdict stop">{d.error}</p> : <Json value={d.result} />}
            </div>
          </div>
        </>
      );
    case "draft":
      return <blockquote className="letter">{d.draft}</blockquote>;
    case "verify":
      return (
        <>
          <dl className="fields">
            {d.checked_by && <Field label="Read by">{modelName(d.checked_by)}{d.checker_fell_back ? ", standing in" : ""}</Field>}
            {d.failed_checks?.length > 0 && <Field label="Failed">{d.failed_checks.map(words).join(", ")}</Field>}
          </dl>
          {d.unsupported_claims?.length > 0 && (
            <ul className="claims">
              {d.unsupported_claims.map((claim: string, i: number) => (
                <li key={i}>{claim}</li>
              ))}
            </ul>
          )}
        </>
      );
    case "escalate":
      return (
        <>
          {(d.signals ?? []).map((s: string) => (
            <p key={s} className="verdict person">
              Stopped before any plan was made: {s}.
            </p>
          ))}
          <dl className="fields">
            <Field label="Why">{words(String(d.terminal_reason ?? "").replace("escalated_", ""))}</Field>
            {d.priority && <Field label="Priority">{d.priority}</Field>}
            {d.respond_within_hours && <Field label="Reply promised within">{d.respond_within_hours} hours</Field>}
            {d.error && <Field label="Queue error">{d.error}</Field>}
          </dl>
        </>
      );
    default:
      return null;
  }
}

export function StepCard({ step, index }: { step: Step; index: number }) {
  const { text, tone } = summary(step);
  return (
    <li className={`step ${tone}`}>
      <span className="dot" aria-hidden="true" />
      <div className="step-head">
        <span className="step-node">
          <span className="step-index">{index + 1}</span>
          {TITLES[step.node] ?? step.node}
        </span>
        <span className="step-text">{text}</span>
        <span className="step-time">{step.node === "await_approval" && step.took_ms ? `waited ${duration(step.took_ms)}` : duration(step.took_ms)}</span>
      </div>
      <div className="step-body">
        <Detail step={step} />
      </div>
    </li>
  );
}
