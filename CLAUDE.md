# Deflect — Project Context

> Put this file at the repo root. Claude Code reads it automatically at the start of every session.
> The `PHASE_*.md` files are session briefs — paste one in when you start that phase.

## What this project is

Deflect is a support-operations agent with a reliability harness. It reads a customer
support ticket, retrieves the relevant policy and the customer's real order, decides
whether to answer / act / escalate, executes permitted actions through an MCP server,
and writes the reply. Around the agent sit three things that matter as much as the agent:

1. **Evals** — a 120-case golden dataset and a metrics harness, gated in CI.
2. **Guardrails** — tool-layer authorization, value thresholds, human-approval interrupts.
3. **Observability** — Langfuse via OpenTelemetry, self-hosted.

This is a portfolio project. The goal is not a product; it is *demonstrable evidence of
production agent engineering*. When a choice is between "more features" and "more
measurable", choose measurable.

## Non-negotiable design rules

- `agent/` NEVER imports from `mcp_server/`. It connects over the MCP protocol like any
  other client. This is what makes the server independently publishable.
- Guardrails are enforced in **Python, before the tool call**. Never by prompt instruction.
- Every mutating tool call carries an idempotency key. Uniqueness is enforced by a database
  constraint, not application logic.
- `issue_refund` requires a `policy_doc_id` argument that must resolve to a real policy
  document. An ungrounded refund must be impossible at the protocol level.
- PII redaction runs before the first model call AND before anything is written to traces.
  The rehydration map is in-memory only, never persisted.
- The model provider is swappable via config from day one. Never hardcode Ollama or a
  hosted API anywhere outside `agent/providers.py`.
- Two separate counters: `loop_count` (tool-call convergence) and `retry_count`
  (verification). Do not merge them.

## Stack

| Layer | Choice |
| --- | --- |
| Language | Python 3.11+ |
| Orchestration | LangGraph |
| API | FastAPI |
| Tools | Python MCP SDK (stdio + HTTP) |
| Model | Ollama default, hosted API switchable |
| Vector store | Qdrant |
| Database | Postgres |
| Tracing | Langfuse self-hosted, instrumented via OpenTelemetry |
| Evals | pytest + custom harness |
| CI | GitHub Actions |
| Console | React + Vite |

## Repo layout

```
deflect/
  agent/
    graph.py            # LangGraph wiring, edges, compile
    state.py            # TicketState + all Pydantic models
    providers.py        # Ollama / hosted switch — the ONLY place provider is named
    mcp_client.py       # the only way the agent reaches the MCP server
    tracing.py          # OpenTelemetry spans, redacted — the ONLY place a trace backend is named
    approvals.py        # approval requests, decided once, then the paused run resumes
    nodes/
      redact.py classify.py retrieve.py plan.py guardrail.py approval.py act.py
      draft.py verify.py escalate.py respond.py
    guardrails/
      policy.py         # allowlist matrix, thresholds, numeric policy rules
      redact.py         # PII scrubbing
      audit.py          # append-only log writer
  mcp_server/           # separate publishable package
    server.py
    tools/orders.py tools/policy.py tools/actions.py
    SECURITY.md
  evals/
    golden/tickets.jsonl
    metrics.py judge.py judge_agreement.py run.py gate.py report.py compare.py validate.py
    checker_replay.py   # a different reply checker over recorded drafts, no agent run
    judge_validation/   # the 20 hand graded replies and the sheet they were graded from
  data/
    policies/*.md
    seed_orders.py
    migrate.py migrations/*.sql   # the agent's DB role and the audit log grants
  api/main.py
  console/              # React + Vite
  docs/
    EVALS.md FAILURES.md ARCHITECTURE.md
  .github/workflows/evals.yml
  docker-compose.yml
```

## Conventions

- Every node is `(state: TicketState, runtime: Runtime[RunContext]) -> dict`, returning ONLY
  the keys it wrote. `runtime.context` holds per-run objects that must never be checkpointed:
  the model, the clock, the PII rehydration map and the MCP client.
- The agent reaches orders, policies and actions only through `agent/mcp_client.py`. Business
  errors from a tool are never retried; transport errors are retried once.
- Pydantic models for all structured output. No raw dict parsing of model responses.
- Currency is INR throughout. Field names carry the unit: `amount_inr`, `cost_inr`.
- Timestamps are timezone-aware UTC, ISO 8601 at boundaries.
- Log at INFO for node entry/exit, DEBUG for prompts, WARNING for every guardrail denial.
- Tests live beside the code they test; eval cases live only in `evals/golden/`.
- The agent connects to Postgres as `deflect_app` (`data.db.app_database_url`). Only the owner
  seeds and migrates. Never grant `deflect_app` UPDATE or DELETE on `audit_log`.
- A migration that has been applied is never edited. Add a new numbered file instead.
- Spans are created only through `agent/tracing.py`, which scrubs every value. Never put the
  rehydrated reply or the live tool arguments on a span.
- A fixed failure gets a `regression` tag on its golden ticket and, where it can be counted, a
  count in `evals/metrics.py` that `evals/gate.py` holds at zero.
- Recorded eval runs use a fixed `DEFLECT_SEED_ANCHOR`, so two versions see the same tickets.
- The reply checker's model is `DEFLECT_CHECKER_PROVIDER`, empty for the agent's own. Nodes get it
  from `runtime.context.checker()`. A decider (Jev) is called only through `providers.choose`.
- One recorded version changes one thing. A new checker, a new classifier and a new planner are
  three versions, not one.
- What pol_escalation says must never be acted on, and that can be seen in the message itself, is
  enforced by the guardrail's `escalation_rules` check from `ESCALATION_SIGNALS` in
  `agent/guardrails/policy.py`. The plan's prompt saying the same thing is not the control.
- A guardrail verdict never quotes the customer. It goes to the audit log and the trace.

## Definition of "done" for any phase

The phase file's own checklist passes AND `python -m evals.run --subset smoke` executes
without error. If a phase breaks the eval runner, it is not done.
