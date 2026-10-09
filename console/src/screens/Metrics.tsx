import { api } from "../api";
import { Badge, Empty, Problem } from "../components/bits";
import { LineChart } from "../components/LineChart";
import type { Series } from "../components/LineChart";
import { useLoad } from "../hooks";
import type { Target } from "../types";

const SERIES: Series[] = [
  { key: "escalation_recall", label: "Escalation recall", color: "var(--series-1)" },
  { key: "intent_accuracy", label: "Intent accuracy", color: "var(--series-2)" },
  { key: "action_correctness", label: "Action correctness", color: "var(--series-3)" },
  { key: "deflection_rate", label: "Deflection", color: "var(--series-4)" },
  { key: "forbidden_tool_rate", label: "Forbidden tool rate", color: "var(--series-5)" },
];

function met(target: Target) {
  if (target.met === null) return <span className="muted">reported</span>;
  return target.met ? <Badge tone="good">met</Badge> : <Badge tone="stop">not met</Badge>;
}

function number(value: number | null | undefined) {
  return value === null || value === undefined ? "n/a" : value.toFixed(2);
}

export function Metrics() {
  const { data, error, loading } = useLoad(api.metrics, []);
  if (loading) return <p className="muted">Loading the eval history.</p>;
  if (!data) return <Problem error={error} />;
  const later = data.comparison_versions;

  return (
    <section>
      <h1>Metrics</h1>
      <p className="lede">
        Every number here comes from running the same 120 labelled tickets through the agent. A version is one full run, and a row is never edited
        after it is recorded, so the weak early rows stay.
      </p>
      {!data.latest && <Empty>No version has been recorded yet.</Empty>}
      {data.latest && (
        <>
          <h2>
            The ten targets, as of {data.latest} <span className="muted">on {data.latest_model}</span>
          </h2>
          <div className="scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Metric</th>
                  <th>Target</th>
                  <th className="num">{data.latest}</th>
                  <th>Met</th>
                  <th>In CI</th>
                </tr>
              </thead>
              <tbody>
                {data.targets.map((target) => (
                  <tr key={target.metric}>
                    <td>{target.label}</td>
                    <td>{target.target}</td>
                    <td className="num">{target.shown}</td>
                    <td>{met(target)}</td>
                    <td className="muted">{target.ci}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <h2>Version history</h2>
          <LineChart versions={data.versions} series={SERIES} />
          <div className="scroll">
            <table className="grid">
              <thead>
                <tr>
                  <th>Version</th>
                  <th>What changed</th>
                  <th className="num">Intent</th>
                  <th className="num">Esc. recall</th>
                  <th className="num">Action</th>
                  <th className="num">Forbidden</th>
                  <th className="num">Deflection</th>
                  <th className="num">p95</th>
                </tr>
              </thead>
              <tbody>
                {data.versions.map((version) => (
                  <tr key={version.version}>
                    <td>
                      <code>{version.version}</code>
                    </td>
                    <td>
                      {version.change}
                      <span className="why-line">{version.model}, {version.date}</span>
                    </td>
                    <td className="num">{number(version.metrics.intent_accuracy)}</td>
                    <td className="num">{number(version.metrics.escalation_recall)}</td>
                    <td className="num">{number(version.metrics.action_correctness)}</td>
                    <td className="num">{number(version.metrics.forbidden_tool_rate)}</td>
                    <td className="num">{number(version.metrics.deflection_rate)}</td>
                    <td className="num">{version.metrics.latency_s_p95 === null ? "n/a" : `${version.metrics.latency_s_p95.toFixed(1)} s`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {later.length > 0 && (
        <>
          <h2>
            Versions {later[0].version} to {later[later.length - 1].version}{" "}
            <span className="muted">on {later[later.length - 1].model}</span>
          </h2>
          <p className="muted">
            These versions ran on another setup, so they have a chart of their own and each is read against the one before it. Their rows are in the
            table below.
          </p>
          <LineChart versions={later} series={SERIES} />
        </>
      )}

      {data.comparison.length > 0 && (
        <>
          <h2>The same tickets on different models</h2>
          <div className="scroll">
            <table className="grid wide">
              <thead>
                <tr>
                  {Object.keys(data.comparison[0]).map((heading) => (
                    <th key={heading} className={heading === "Configuration" || heading === "Checker" ? "" : "num"}>
                      {heading}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.comparison.map((row, i) => (
                  <tr key={i}>
                    {Object.entries(row).map(([heading, value]) => (
                      <td key={heading} className={heading === "Configuration" || heading === "Checker" ? "" : "num"}>
                        {value}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      <Problem error={error} />
    </section>
  );
}
