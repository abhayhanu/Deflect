import type { Approval, Config, DemoStatus, Metrics, Run, Ticket } from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter: number | null = null,
  ) {
    super(message);
  }
}

let token = "";

export function setToken(value: string) {
  token = value.trim();
  try {
    sessionStorage.setItem("deflect_token", token);
  } catch {
    // A browser that blocks storage still works, the token just has to be typed again.
  }
}

export function savedToken(): string {
  if (token) return token;
  try {
    token = sessionStorage.getItem("deflect_token") ?? "";
  } catch {
    token = "";
  }
  return token;
}

async function call<T>(path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  if (savedToken()) headers["Authorization"] = `Bearer ${savedToken()}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const reply = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!reply.ok) {
    let detail = reply.statusText;
    try {
      const data = await reply.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      // The body was not JSON, so the status text is all there is.
    }
    const wait = Number(reply.headers.get("retry-after"));
    throw new ApiError(reply.status, detail, Number.isFinite(wait) && wait > 0 ? wait : null);
  }
  return reply.json() as Promise<T>;
}

export interface NewTicket {
  raw_message: string;
  customer_id: string | null;
  channel: string;
}

export const api = {
  config: () => call<Config>("/config"),
  tickets: () => call<Ticket[]>("/tickets"),
  run: (id: string) => call<Run>(`/tickets/${encodeURIComponent(id)}/run`),
  submit: (ticket: NewTicket) => call<{ ticket_id: string }>("/tickets", ticket),
  approvals: () => call<Approval[]>("/approvals"),
  decide: (id: string, approve: boolean, approver_id: string, note: string) =>
    call<{ ticket_id: string }>(`/approvals/${encodeURIComponent(id)}/${approve ? "approve" : "deny"}`, {
      approver_id,
      note: note.trim() || null,
    }),
  metrics: () => call<Metrics>("/metrics"),
  demo: () => call<DemoStatus>("/demo"),
  resetDemo: () => call<DemoStatus>("/demo/reset", {}),
};
