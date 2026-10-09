import { useState } from "react";
import { api, ApiError } from "../api";
import { Badge, Empty, Problem } from "../components/bits";
import { cost, duration, outcome, when, words } from "../format";
import { go, useLoad } from "../hooks";
import type { Config, DemoStatus, Sample } from "../types";

function NewTicket({ samples, onSent }: { samples: Sample[]; onSent: (ticketId: string) => void }) {
  const [message, setMessage] = useState("");
  const [customer, setCustomer] = useState("");
  const [channel, setChannel] = useState("web_form");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const customerOk = customer === "" || /^C_\d{4}$/.test(customer);

  function fill(sample: Sample) {
    setMessage(sample.raw_message);
    setCustomer(sample.customer_id ?? "");
    setChannel(sample.channel);
  }

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const sent = await api.submit({ raw_message: message, customer_id: customer || null, channel });
      setMessage("");
      onSent(sent.ticket_id);
    } catch (problem) {
      setError(problem as Error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="card new-ticket">
      <summary>Send a ticket through the agent</summary>
      {samples.length > 0 && (
        <p className="samples">
          Try one:
          {samples.map((sample) => (
            <button key={sample.label} className="link" onClick={() => fill(sample)}>
              {sample.label}
            </button>
          ))}
        </p>
      )}
      <label>
        The customer's message
        <textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={4} maxLength={8000} placeholder="Where is my order A3107?" />
      </label>
      <div className="row">
        <label>
          Customer id
          <input value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder="C_1140" aria-invalid={!customerOk} />
        </label>
        <label>
          Channel
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="web_form">web form</option>
            <option value="email">email</option>
            <option value="chat">chat</option>
          </select>
        </label>
        <button className="primary" disabled={busy || !message.trim() || !customerOk} onClick={send}>
          {busy ? "Running" : "Run it"}
        </button>
      </div>
      {busy && <p className="muted small">The agent is working. A hosted model takes a few seconds, a local one can take minutes.</p>}
      <Problem error={error} />
    </details>
  );
}

function DemoBanner({ status, onReset }: { status: DemoStatus; onReset: () => void }) {
  const [error, setError] = useState<Error | null>(null);

  async function reset() {
    setError(null);
    try {
      await api.resetDemo();
      onReset();
    } catch (problem) {
      setError(problem as Error);
    }
  }

  const minutes = Math.ceil(status.reset_in_s / 60);
  return (
    <aside className="card demo">
      <p>
        <strong>This is a live demo on made up shop data.</strong> The tickets below were run through the real agent when the demo started. Open any
        of them to see every step it took. One refund is waiting in the approval queue for you to approve or deny. Anyone can read what is
        sent here, so please do not type real personal details.
      </p>
      {status.loading && (
        <p className="muted">
          Loading the demo tickets, {status.loaded} of {status.total} done.
        </p>
      )}
      {status.error && <p className="problem">{status.error}</p>}
      <p className="muted small">
        {status.budget_inr !== null && `Model budget used today: Rs ${status.spent_inr.toFixed(2)} of Rs ${status.budget_inr.toFixed(0)}. `}
        <button className="link" disabled={status.loading || status.reset_in_s > 0} onClick={reset}>
          Reset the demo
        </button>
        {status.reset_in_s > 0 && ` can be used again in ${minutes} min.`}
      </p>
      <Problem error={error} />
    </aside>
  );
}

export function Inbox({ config }: { config: Config }) {
  const tickets = useLoad(api.tickets, [], 5000);
  const demo = useLoad<DemoStatus | null>(() => (config.demo ? api.demo() : Promise.resolve(null)), [config.demo], config.demo ? 3000 : 0);
  const locked = tickets.error instanceof ApiError && tickets.error.status === 401;

  return (
    <section>
      <h1>Inbox</h1>
      {demo.data && <DemoBanner status={demo.data} onReset={() => { demo.reload(); tickets.reload(); }} />}
      <NewTicket samples={config.samples} onSent={(id) => go(`/tickets/${encodeURIComponent(id)}`)} />
      {!locked && <Problem error={tickets.error} />}
      {!tickets.loading && tickets.data?.length === 0 && <Empty>No tickets yet. Send one through the form above.</Empty>}
      {tickets.data && tickets.data.length > 0 && (
        <table className="grid inbox">
          <thead>
            <tr>
              <th>Ticket</th>
              <th>Intent</th>
              <th>Outcome</th>
              <th className="num">Cost</th>
              <th className="num">Took</th>
              <th className="num">Received</th>
            </tr>
          </thead>
          <tbody>
            {tickets.data.map((ticket) => {
              const result = outcome(ticket);
              const link = `#/tickets/${encodeURIComponent(ticket.ticket_id)}`;
              return (
                <tr key={ticket.ticket_id} className="clickable" onClick={() => (window.location.hash = link)}>
                  <td className="ticket-cell">
                    <a href={link} onClick={(e) => e.stopPropagation()}>
                      <code>{ticket.ticket_id}</code>
                    </a>
                    <span className="preview">{ticket.preview}</span>
                  </td>
                  <td>{words(ticket.intent)}</td>
                  <td>
                    <Badge tone={result.tone}>{result.label}</Badge>
                    {result.why && <span className="why-line">{result.why}</span>}
                  </td>
                  <td className="num">{cost(ticket.cost_inr)}</td>
                  <td className="num">{duration(ticket.latency_ms)}</td>
                  <td className="num">{when(ticket.created_at)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}
