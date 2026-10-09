import { useState } from "react";
import type { Version } from "../types";

export interface Series {
  key: string;
  label: string;
  color: string;
}

const W = 760;
const H = 300;
const PAD = { top: 16, right: 150, bottom: 34, left: 40 };

// Every series here is a share between 0 and 1, so they can sit on one axis.
export function LineChart({ versions, series }: { versions: Version[]; series: Series[] }) {
  const [hover, setHover] = useState<number | null>(null);
  if (versions.length === 0) return null;
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const x = (i: number) => PAD.left + (versions.length === 1 ? innerW / 2 : (i * innerW) / (versions.length - 1));
  const y = (value: number) => PAD.top + innerH - value * innerH;
  const last = versions.length - 1;
  // Versions read from the model comparison have no change written down, only their numbers.
  const hint = versions.some((v) => v.change) ? "Hover a version to see what changed in it." : "Hover a version to see its numbers.";

  // Labels at the right edge are nudged apart so two lines that end close together stay readable.
  const ends = series
    .map((s) => ({ ...s, value: versions[last].metrics[s.key] }))
    .filter((s): s is Series & { value: number } => s.value !== null && s.value !== undefined)
    .sort((a, b) => b.value - a.value);
  let previous = -Infinity;
  const labelY = new Map<string, number>();
  for (const end of ends) {
    const wanted = Math.max(y(end.value), previous + 14);
    labelY.set(end.key, wanted);
    previous = wanted;
  }

  return (
    <figure className="chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Eval metrics for every recorded version" onMouseLeave={() => setHover(null)}>
        {[0, 0.25, 0.5, 0.75, 1].map((tick) => (
          <g key={tick}>
            <line className="gridline" x1={PAD.left} x2={PAD.left + innerW} y1={y(tick)} y2={y(tick)} />
            <text className="axis" x={PAD.left - 8} y={y(tick) + 4} textAnchor="end">
              {tick.toFixed(2)}
            </text>
          </g>
        ))}
        {versions.map((version, i) => (
          <g key={version.version}>
            <text className="axis" x={x(i)} y={H - 12} textAnchor="middle">
              {version.version}
            </text>
            <rect x={x(i) - innerW / versions.length / 2} y={PAD.top} width={innerW / versions.length} height={innerH} fill="transparent" onMouseEnter={() => setHover(i)} />
          </g>
        ))}
        {hover !== null && <line className="cursor" x1={x(hover)} x2={x(hover)} y1={PAD.top} y2={PAD.top + innerH} />}
        {series.map((s) => {
          const points = versions.map((v, i) => ({ i, value: v.metrics[s.key] })).filter((p): p is { i: number; value: number } => p.value !== null && p.value !== undefined);
          const path = points.map((p, n) => `${n === 0 ? "M" : "L"}${x(p.i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ");
          return (
            <g key={s.key}>
              <path d={path} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" />
              {points.map((p) => (
                <circle key={p.i} cx={x(p.i)} cy={y(p.value)} r={hover === p.i ? 4.5 : 3} fill={s.color}>
                  <title>{`${versions[p.i].version} ${s.label}: ${p.value.toFixed(2)}`}</title>
                </circle>
              ))}
              {labelY.has(s.key) && (
                <text className="end-label" x={PAD.left + innerW + 10} y={(labelY.get(s.key) ?? 0) + 4} fill={s.color}>
                  {s.label} {versions[last].metrics[s.key]?.toFixed(2)}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      <figcaption>
        {hover === null ? (
          hint
        ) : (
          <>
            <strong>{versions[hover].version}</strong>
            {versions[hover].change ? ` ${versions[hover].change}.` : "."}{" "}
            {series.map((s) => `${s.label} ${versions[hover].metrics[s.key]?.toFixed(2) ?? "not measured"}`).join(", ")}.
          </>
        )}
      </figcaption>
    </figure>
  );
}
