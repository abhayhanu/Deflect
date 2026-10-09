import { useState } from "react";
import { api, ApiError, savedToken, setToken } from "./api";
import { Problem } from "./components/bits";
import { modelName } from "./format";
import { useLoad, useRoute } from "./hooks";
import { Approvals } from "./screens/Approvals";
import { Inbox } from "./screens/Inbox";
import { Metrics } from "./screens/Metrics";
import { RunView } from "./screens/RunView";

function TokenGate({ onSaved }: { onSaved: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<Error | null>(null);

  async function save() {
    setToken(value);
    try {
      await api.tickets();
      onSaved();
    } catch (problem) {
      setToken("");
      setError(problem instanceof ApiError && problem.status === 401 ? new Error("That token was not accepted.") : (problem as Error));
    }
  }

  return (
    <section className="card gate">
      <h1>This console needs a token</h1>
      <p className="muted">It is the value of DEFLECT_API_TOKEN on the server. It is kept for this browser tab only.</p>
      <div className="row">
        <input type="password" value={value} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => e.key === "Enter" && save()} aria-label="API token" />
        <button className="primary" disabled={!value.trim()} onClick={save}>
          Open the console
        </button>
      </div>
      <Problem error={error} />
    </section>
  );
}

export function App() {
  const route = useRoute();
  const config = useLoad(api.config, []);
  const [unlocked, setUnlocked] = useState(() => savedToken() !== "");
  const [approver, setApprover] = useState<string | null>(null);
  const needsToken = config.data?.token_required === true && !unlocked;
  const approvals = useLoad(() => (config.data && !needsToken ? api.approvals() : Promise.resolve([])), [config.data, needsToken, route.screen, route.id], 5000);

  if (config.loading) return <p className="muted boot">Connecting to the Deflect API.</p>;
  if (!config.data) {
    return (
      <main className="boot">
        <h1>The Deflect API did not answer</h1>
        <p className="muted">Start it with uvicorn, then reload this page.</p>
        <Problem error={config.error} />
      </main>
    );
  }

  const settings = config.data;
  const name = approver ?? (settings.demo ? "demo_visitor" : "");
  const waiting = approvals.data?.length ?? 0;
  const tab = (screen: string, href: string, label: string, count = 0) => (
    <a href={href} className={route.screen === screen || (screen === "inbox" && route.screen === "ticket") ? "active" : ""}>
      {label}
      {count > 0 && <span className="count">{count}</span>}
    </a>
  );

  return (
    <>
      <header className="top">
        <a href="#/" className="brand">
          Deflect
        </a>
        <nav>
          {tab("inbox", "#/", "Inbox")}
          {tab("approvals", "#/approvals", "Approvals", waiting)}
          {tab("metrics", "#/metrics", "Metrics")}
        </nav>
        <span className="models" title="The model that plans and writes, the one that classifies, and the one that checks each reply">
          {modelName(settings.models.agent)}
          {settings.models.classifier !== settings.models.agent && ` · classifier ${modelName(settings.models.classifier)}`}
          {settings.models.checker !== settings.models.agent && ` · checker ${modelName(settings.models.checker)}`}
        </span>
      </header>
      <main>
        {needsToken ? (
          <TokenGate onSaved={() => setUnlocked(true)} />
        ) : route.screen === "ticket" && route.id ? (
          <RunView id={route.id} config={settings} approver={name} onApprover={setApprover} />
        ) : route.screen === "approvals" ? (
          <Approvals config={settings} approver={name} onApprover={setApprover} />
        ) : route.screen === "metrics" ? (
          <Metrics />
        ) : (
          <Inbox config={settings} />
        )}
      </main>
      <footer className="foot">
        A support operations agent with a reliability harness. Guardrails run in code before any tool is called, and every number on the metrics screen
        comes from the eval suite.
      </footer>
    </>
  );
}
