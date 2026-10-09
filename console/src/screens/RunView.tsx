import { useState } from "react";
import { api } from "../api";
import { ApprovalCard } from "../components/ApprovalCard";
import { Badge, Json, Problem } from "../components/bits";
import { StepCard } from "../components/StepCard";
import { cost, duration, modelName, outcome, when, words } from "../format";
import { useLoad } from "../hooks";
import type { AuditRow, Config } from "../types";

interface Props {
  id: string;
  config: Config;
  approver: string;
  onApprover: (name: string) => void;
}

function AuditTable({ rows }: { rows: AuditRow[] }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <div className="scroll">
    <table className="grid audit">
      <thead>
        <tr>
          <th>Tool</th>
          <th>Authorized by</th>
          <th>Outcome</th>
          <th className="num">Took</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const denied = row.authorized_by === "denied";
          return [
            <tr key={i} className="clickable" onClick={() => setOpen(open === i ? null : i)}>
              <td>
                <code>{row.tool_name}</code>
              </td>
              <td>{denied ? <Badge tone="stop">denied</Badge> : row.authorized_by === "human" ? <Badge tone="wait">{row.approver_id}</Badge> : "policy"}</td>
              <td>{denied ? `${words(row.check_name)}: ${row.error}` : row.error ? row.error : "ok"}</td>
              <td className="num">{denied ? "" : duration(row.latency_ms)}</td>
            </tr>,
            open === i && (
              <tr key={`${i}-open`} className="opened">
                <td colSpan={4}>
                  <div className="pair">
                    <div>
                      <h4>Arguments</h4>
                      <Json value={row.tool_args} />
                    </div>
                    <div>
                      <h4>What the log kept</h4>
                      <Json value={row.result} />
                    </div>
                  </div>
                </td>
              </tr>
            ),
          ];
        })}
      </tbody>
    </table>
    </div>
  );
}

export function RunView({ id, config, approver, onApprover }: Props) {
  const { data: run, error, loading, reload } = useLoad(() => api.run(id), [id], 4000);
  if (loading && !run) return <p className="muted">Loading the run.</p>;
  if (!run) return <Problem error={error} />;

  const { ticket } = run;
  const { models } = config;
  const result = outcome(ticket);
  const pending = run.approval?.status === "pending" ? run.approval : null;

  return (
    <section className="run">
      <a href="#/" className="back">
        Back to the inbox
      </a>
      <header className="run-head">
        <h1>
          <code>{ticket.ticket_id}</code>
        </h1>
        <Badge tone={result.tone}>{result.label}</Badge>
        {result.why && <span className="why">because {result.why}</span>}
      </header>
      {run.note && <p className="note">What this one shows: {run.note}.</p>}
      <dl className="fields facts">
        <div className="field">
          <dt>Intent</dt>
          <dd>{words(ticket.intent) || "not classified"}</dd>
        </div>
        <div className="field">
          <dt>Model cost</dt>
          <dd>{cost(ticket.cost_inr)}</dd>
        </div>
        <div className="field">
          <dt>Time in the agent</dt>
          <dd>{duration(ticket.latency_ms) || "not recorded"}</dd>
        </div>
        <div className="field">
          <dt>Received</dt>
          <dd>{when(ticket.created_at)}</dd>
        </div>
        <div className="field">
          <dt>Models</dt>
          <dd>
            {modelName(models.agent)}
            {models.classifier !== models.agent && `, classified by ${modelName(models.classifier)}`}
            {models.checker !== models.agent && `, checked by ${modelName(models.checker)}`}
          </dd>
        </div>
        <div className="field">
          <dt>Trace</dt>
          <dd>
            {run.trace_url ? (
              <a href={run.trace_url} target="_blank" rel="noreferrer">
                Open the trace
              </a>
            ) : run.trace_id ? (
              <code title="Set DEFLECT_TRACE_URL to turn this into a link">{run.trace_id}</code>
            ) : (
              "tracing is off"
            )}
          </dd>
        </div>
      </dl>

      <div className="letters">
        <div>
          <h2>What the customer wrote</h2>
          <blockquote className="letter">{run.message}</blockquote>
          <p className="muted small">Shown as the agent saw it, with personal details replaced by placeholders.</p>
        </div>
        <div>
          <h2>What the customer was sent</h2>
          {run.reply ? <blockquote className="letter">{run.reply}</blockquote> : <p className="empty">{pending ? "Nothing yet. The run is waiting for a person." : "Nothing was sent."}</p>}
        </div>
      </div>

      {pending && (
        <>
          <h2>Waiting for a person</h2>
          <ApprovalCard approval={pending} approver={approver} ceiling={config.auto_approve_refund_inr} onApprover={onApprover} onDecided={reload} linkToTicket={false} />
        </>
      )}

      <h2>The path it took</h2>
      <ol className="steps">
        {run.steps.map((step, i) => (
          <StepCard key={i} step={step} index={i} />
        ))}
        {pending && (
          <li className="step wait ghost">
            <span className="dot" aria-hidden="true" />
            <div className="step-head">
              <span className="step-node">
                <span className="step-index">{run.steps.length + 1}</span>Approval
              </span>
              <span className="step-text">Paused here. Nothing is held in memory, the run is saved and continues when someone decides.</span>
            </div>
          </li>
        )}
      </ol>

      <h2>The audit log for this ticket</h2>
      <p className="muted small">Every tool call, read or write, every refusal and every decision by a person. The agent's database role can add rows here and can never change or remove one.</p>
      {run.audit.length ? <AuditTable rows={run.audit} /> : <p className="empty">No tool was called.</p>}
      <Problem error={error} />
    </section>
  );
}
