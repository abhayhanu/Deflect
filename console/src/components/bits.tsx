import type { ReactNode } from "react";
import { ApiError } from "../api";
import type { Tone } from "../format";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">nothing</span>;
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function Problem({ error }: { error: Error | null }) {
  if (!error) return null;
  const wait = error instanceof ApiError && error.retryAfter ? ` Try again in ${error.retryAfter} s.` : "";
  return (
    <p className="problem" role="alert">
      {error.message}
      {wait}
    </p>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}
