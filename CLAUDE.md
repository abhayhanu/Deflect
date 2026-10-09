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
3. **Observability** — OpenTelemetry traces, sent to LangSmith. Langfuse is the same exporter
   with another address and is not used.

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
| Tracing | OpenTelemetry, exported to LangSmith. Langfuse supported, not used |
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
    classifier_replay.py  # a different classifier over the golden messages, no agent run
    components.py       # every step scored on its own from a recorded run, no model
    isolate.py          # one step run alone, the steps before it replaced by the labels
    sources.py          # the order a recorded ticket was about, read back for older results files
    history.py          # the tables in EVALS.md read back as data, for the console's metrics screen
    stability.py        # the same tickets across several runs of the same code, and what decided each
    judge_validation/   # the 20 hand graded replies and the sheet they were graded from
  data/
    policies/*.md
    seed_orders.py
    migrate.py migrations/*.sql   # the agent's DB role and the audit log grants
  api/
    main.py             # every HTTP route, and the built console served at the root
    runs.py             # the one place a ticket is run or resumed for the API: trace, inbox row, budget
    runview.py          # the run view, rebuilt from the checkpoints and the audit log
    tickets.py          # the inbox: one summary row per ticket
    limits.py           # the rate limit and the daily model budget
    demo.py             # the ten preloaded tickets of the public demo, and its reset
  console/              # React + Vite + TypeScript: inbox, run view, approval queue, metrics
  deploy/               # start.sh for the one container demo, and the Space export
  docs/
    EVALS.md FAILURES.md ARCHITECTURE.md DEPLOY.md
  .github/workflows/evals.yml
  docker-compose.yml
  Dockerfile            # the demo image: API, console, Postgres and Qdrant in one container
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
- The classifier's model is `DEFLECT_CLASSIFIER_PROVIDER`, empty for the agent's own. Nodes get it
  from `runtime.context.classifier()`. `Classification` is the schema a chat model fills in, so
  who classified and the intent probabilities live in the state beside it, never inside it.
- A new checker or classifier is replayed first (`evals/checker_replay.py`,
  `evals/classifier_replay.py`) and only then given a full run.
- A recorded version has no stand ins. `classifier_fallbacks` and `checker_fallbacks` are gated at
  zero, and `evals/report.py` refuses a run that has any.
- There are three levels of evaluation and each answers a different question. A full run is the
  system. `evals/components.py` is each step on the inputs it really got. `evals/isolate.py` is
  one step on perfect inputs, alone or with the real retriever in front of it. Only a full run
  is a version.
- One recorded version changes one thing. A new checker, a new classifier and a new planner are
  three versions, not one.
- What pol_escalation says must never be acted on, and that can be seen in the message itself, is
  enforced by the guardrail's `escalation_rules` check from `ESCALATION_SIGNALS` in
  `agent/guardrails/policy.py`. The plan's prompt saying the same thing is not the control.
- The same signals are read straight after classify by `early_escalation`, before retrieve and
  plan, so a ticket the plan would have answered is stopped too. A new signal is added to
  `ESCALATION_SIGNALS` only, never as a branch in a node, and never to make one golden ticket pass.
  `signal_tickets_handled` is gated at zero.
- A policy that overrides the others is marked `pinned: true` in its front matter, and
  `search_policy` returns it for every query, after the sections it ranked. Today that is
  `pol_escalation` only. Pinning is not a way to fix a search miss on an ordinary policy.
- The policy index rebuilds itself when the policy files differ from what it holds. A new field
  in the front matter needs no manual reindex.
- One run is one sample. A hosted model does not plan the same way twice, and 30 tickets must
  escalate, so a setup is trusted on the lowest of several passes. `--subset escalation` runs
  those 30 alone and `evals/stability.py` compares the passes. A replay over a recorded run is a
  prediction and is never written down as a result.
- In a schema a model fills in, the reasoning comes before the fields that depend on it. A model
  writes the fields in order and cannot go back, so `Plan.rationale` is first, and a contract
  test keeps it there. `Classification.reasoning` is still last. It matters only when a chat model
  classifies, not Jev, and moving it is a version of its own.
- Wording from a prompt must never reach a customer. `respond` removes the one instruction a
  model has echoed, and `instruction_echoes` is gated at zero.
- A guardrail verdict never quotes the customer. It goes to the audit log and the trace.
- Anything that grades a reply, the checker, the judge or a person, is shown the order record.
  A grader without it cannot tell a true statement about the order from an invented one.
- The API runs and resumes tickets only through `api/runs.py`, so every run is traced, filed in
  the inbox and counted against the budget. A route never calls `graph.invoke` itself.
- The console never shows a personal detail. Inbox previews are the redacted message, and
  everything `api/runview.py` returns goes through redaction again, the reply included. Only
  the submit, read and decide routes that return a `TicketOut` carry the customer's real reply,
  for whoever delivers it, and in the demo that one is redacted too.
- The run view is derived, not stored. It is rebuilt from the checkpoints and the audit log on
  every read. Do not add a table that records steps.
- The `tickets` table is a summary for the inbox. The checkpoints are the truth, and a row that
  disagrees with its run is rebuilt from the run.
- The console's metrics come from `docs/EVALS.md` through `evals/history.py`. Never give the
  console a second copy of a number.
- Everything that can cost a model call sits behind `may_start_a_run` in `api/main.py`: the rate
  limit, the daily budget and the demo's loading flag. Reading is never limited.
- The judge's rubric has a version, `RUBRIC_VERSION` in `evals/judge.py`. Scores from two
  versions are never averaged, and a change to the rubric needs a new judge validation.

## Definition of "done" for any phase

The phase file's own checklist passes AND `python -m evals.run --subset smoke` executes
without error. If a phase breaks the eval runner, it is not done.
