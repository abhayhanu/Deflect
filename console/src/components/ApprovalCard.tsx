import { useState } from "react";
import { api } from "../api";
import { rupees, when } from "../format";
import type { Approval } from "../types";
import { Field, Json, Problem } from "./bits";

interface Props {
  approval: Approval;
  approver: string;
  ceiling: number;
  onApprover: (name: string) => void;
  onDecided: (ticketId: string) => void;
  linkToTicket?: boolean;
}

export function ApprovalCard({ approval, approver, ceiling, onApprover, onDecided, linkToTicket = true }: Props) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const args = approval.tool_args;
  const { order_id, amount_inr, policy_doc_id, reason_code, ...rest } = args as Record<string, unknown>;
  const nameOk = /^[A-Za-z0-9_.:-]{1,64}$/.test(approver);

  async function decide(approve: boolean) {
    setBusy(approve ? "approve" : "deny");
    setError(null);
    try {
      await api.decide(approval.approval_id, approve, approver, note);
      onDecided(approval.ticket_id);
    } catch (problem) {
      setError(problem as Error);
      setBusy(null);
    }
  }

  return (
    <article className="card approval">
      <header>
        <h3>
          <code>{approval.tool_name}</code>
          {amount_inr !== undefined && <span className="amount">{rupees(amount_inr)}</span>}
        </h3>
        {linkToTicket && <a href={`#/tickets/${encodeURIComponent(approval.ticket_id)}`}>Open the run for {approval.ticket_id}</a>}
      </header>
      <p className="verdict wait">{approval.reason ?? `Above the ${rupees(ceiling)} auto approve ceiling.`}</p>
      <dl className="fields">
        {order_id !== undefined && <Field label="Order">{String(order_id)}</Field>}
        {policy_doc_id !== undefined && (
          <Field label="Policy cited">
            <code>{String(policy_doc_id)}</code>
          </Field>
        )}
        {reason_code !== undefined && <Field label="Reason code">{String(reason_code).replace(/_/g, " ")}</Field>}
        <Field label="Asked">{when(approval.requested_at)}</Field>
      </dl>
      {Object.keys(rest).length > 0 && <Json value={rest} />}
      <p className="muted small">
        Every other guardrail check already passed. If you approve, the checks run once more on freshly read order data before the tool is called.
      </p>
      <div className="decide">
        <label>
          Your name
          <input value={approver} onChange={(e) => onApprover(e.target.value)} placeholder="priya" aria-invalid={!nameOk} />
        </label>
        <label className="grow">
          Note, optional
          <input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Why you decided this way" />
        </label>
        <button className="primary" disabled={!nameOk || busy !== null} onClick={() => decide(true)}>
          {busy === "approve" ? "Approving" : "Approve"}
        </button>
        <button className="danger" disabled={!nameOk || busy !== null} onClick={() => decide(false)}>
          {busy === "deny" ? "Denying" : "Deny"}
        </button>
      </div>
      {!nameOk && <p className="muted small">A name is letters, digits, dots or underscores, with no spaces.</p>}
      <Problem error={error} />
    </article>
  );
}
