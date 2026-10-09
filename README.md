# Deflect

A support operations agent with a reliability harness. It reads a customer ticket, looks up the policy and the real order, decides whether to answer, act or escalate, and acts through its own MCP server, with evals, guardrails and tracing around it.

[![evals](https://github.com/abhayhanu/Deflect/actions/workflows/evals.yml/badge.svg)](https://github.com/abhayhanu/Deflect/actions/workflows/evals.yml)
[![tests](https://github.com/abhayhanu/Deflect/actions/workflows/tests.yml/badge.svg)](https://github.com/abhayhanu/Deflect/actions/workflows/tests.yml)

> **Status:** Phase 5 of 5, in progress. On 120 labelled tickets with a local 7B model: intent accuracy 0.93, escalation recall 1.00 (30 of 30), forbidden tool rate 0.00 including 18 adversarial tickets, deflection 0.23. On a hosted model the same tickets take 6.6 s at p95 and Rs 0.17 each, and the latest run passes the gate with escalation recall 1.00, 30 of 30, in four runs out of four. It took two failed runs to get there, [docs/FAILURES.md](docs/FAILURES.md) entries 17 and 18, and the last fix cost usefulness: deflection fell from 0.61 to 0.54, entry 21. Every pull request runs a 30 ticket eval and fails if a safety number drops. Every ticket is one OpenTelemetry trace with personal details removed before it leaves the process. Every version's scores are in [docs/EVALS.md](docs/EVALS.md), what went wrong along the way is in [docs/FAILURES.md](docs/FAILURES.md), and how it fits together is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## See a ticket being handled

```bash
uvicorn api.main:app --port 8000        # the API
cd console && npm install && npm run dev   # the console, on http://localhost:5173
```

The console has four screens.

- **Inbox.** Every ticket with its intent, how it ended and what it cost.
- **Run view.** The path one ticket took, node by node: what was redacted, how it was classified, which policy sections were found, what the plan chose, what the guardrail said, the tool call with its arguments and result, the draft, the check, the reply. A refused action is shown with all ten guardrail checks in the order they ran, marked passed, blocked or not reached.
- **Approval queue.** Refunds waiting for a person, with the amount, the policy cited and approve and deny.
- **Metrics.** The ten targets against the last recorded version, every version on one chart, and the same tickets on different models.

The run view is not a log that was written on the side. It is read back from the LangGraph checkpoints and the audit log each time, so it cannot say something the run did not do. It never shows a personal detail: the inbox and the run view are sent the same placeholders the model got.

`npm run build` in `console/` writes `console/dist`, and from then on the API serves the console itself at http://localhost:8000.

## The live demo

`DEFLECT_DEMO=1` loads ten tickets at startup: clean answers, actions, a refund that waits for approval, a safety report and an injection attempt. It also turns on a stricter rate limit and a daily model budget. The `Dockerfile` builds one image with the API, the console, Postgres and Qdrant in it. [docs/DEPLOY.md](docs/DEPLOY.md) has the steps for Google Cloud Run and for a Hugging Face Space.

## Quick start

```bash
cp .env.example .env
docker compose up -d
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
ollama pull qwen2.5:7b

python -m data.seed_orders --reset      # also applies the migrations
python -m data.index_policies
python -m agent.cli --case gold_011
python -m evals.run --subset smoke --reseed
```

The agent starts the MCP server for you over stdio. To run the server on its own, see [mcp_server/README.md](mcp_server/README.md).

To use a hosted model instead, run `pip install -e .[hosted]`, then set `DEFLECT_PROVIDER` and the matching API key in `.env`. No code changes.

## Measured on every change

```bash
python -m evals.run --subset smoke --reseed --out results.json
python -m evals.gate results.json          # exits 1 and names the metric if a threshold is breached
python -m evals.judge results.json         # a hosted model scores the replies, needs an API key
```

The gate is what CI runs on every pull request. Escalation recall and the forbidden tool rate are hard gates. Deflection, the share of tickets resolved without a person, is reported and deliberately never gated: gating it would push every future change toward resolving more, which is how an agent ends up refunding things that needed a human. Deflection is what gets reported, escalation recall is what gets defended.

The judge scores replies only, on a fixed rubric. It is shown what the agent was shown, the message, the policy excerpts, the order record and the action taken, and never the labels or the reference reply. Whether the right action ran is measured in code, because a model grading its own family of model is not evidence. The judge itself is checked against 20 replies graded by hand, and the agreement is reported in [docs/EVALS.md](docs/EVALS.md).

## Every failure becomes a permanent test

When a real run shows a failure, it is written up in [docs/FAILURES.md](docs/FAILURES.md) with the tickets, the cause and the fix. The ticket that exposed it is tagged `regression` in the golden set, and where the failure can be counted, the count becomes a metric that the CI gate holds at zero. So the same mistake cannot come back quietly: `python -m evals.run --subset regression --reseed` replays every one of them, and a fix that stops working turns the build red. Seventeen entries so far, among them a refund issued outside its policy window, a reply that announced a cancellation that never happened, a customer who was refunded and never told, and two still open.

## Every ticket is a trace

```bash
# in .env: DEFLECT_TRACE_BACKEND=langsmith and LANGSMITH_API_KEY
python -m agent.cli --case gold_016
```

One trace per ticket, with a span for every node, tool call and model call, carrying tokens, cost, the guardrail's verdict and who authorized each action. It is plain OpenTelemetry, so the backend is only an exporter: `langfuse`, `langsmith` or `both` in `.env`. Personal details are scrubbed twice before a span is exported, and a test decodes what actually goes over the wire to prove it. A refund approved hours later from another process continues the same trace.

## A refund that waits for a person

```bash
python -m agent.cli --case gold_016                # a Rs 3,499 refund, pauses and prints an approval id
python -m agent.cli --approve apr_... --approver priya
```

The pause is a LangGraph `interrupt` saved in Postgres. The first process can be killed while the request waits. The approval is picked up by a new process from the checkpoint, the guardrail runs again on freshly read order data, and only then does the refund go out, recorded as authorized by `priya`. The same flow is available over HTTP:

```bash
uvicorn api.main:app --port 8000
# POST /tickets, GET /approvals, POST /approvals/{id}/approve, POST /approvals/{id}/deny
# GET /tickets, GET /tickets/{id}/run and GET /metrics are what the console reads
```

## Guardrails live in code, before the call

The guardrail node runs before any tool that changes something. It never calls a model. It checks, in order: the tool exists, it is on the allowlist for the ticket's intent, the arguments match the tool's schema exactly, the message is not one the escalation policy sends to a person untouched, the action targets the ticket's own order on the customer's own account, the ticket is within its action and cost budget, no refund exceeds Rs 25,000, the numeric rules in the policy documents hold, and there was no refund on the same order in the last 24 hours. Only then is the Rs 3,000 approval ceiling checked, so a person is never asked to approve something the rules would refuse.

The escalation check exists because of a real failure. A hosted model cancelled and refunded an order for a sender who claimed to be support staff: the order allowed the cancellation, and nothing looked at who was asking. Now a claim of staff authority, an action said to be already approved, an instruction to ignore the rules, a lost account, a legal threat, a safety report or a request for a person stops every changing action, whatever the plan says. It matches the wording the policy itself lists, so it is a floor and not a proof. It is entry 14 in [docs/FAILURES.md](docs/FAILURES.md).

Those situations are read a second time, and earlier. Straight after a ticket is classified, before any policy is searched or any plan is made, the message is matched against the same wording, and a match goes to a person at once. That exists because of a second real failure: the guardrail only runs when the plan wants to act, and a hosted model chose to answer a consumer court threat and a ticket with an instruction hidden in a comment. It is entry 17 in [docs/FAILURES.md](docs/FAILURES.md).

What a signal cannot read is left to the plan, and the plan decides from the policy sections it is shown. So the escalation policy is pinned: the search returns it for every ticket, after the sections it ranked, whether or not the message reads anything like it. Before that, the policy that overrides all the others was found for half of the tickets it governs, and a compensation demand was answered from the shipping policy. It is entry 18. With the pin, the search shows the plan every policy a ticket needs on all 103 tickets that reach it, where it had been 97.

A complaint cannot reach any tool that changes data. A complaint ends in an answer or a human. That is where an agent is most easily talked into a goodwill refund, and closing the path is simpler than defending it.

The table of limits is one file, [agent/guardrails/policy.py](agent/guardrails/policy.py).

## The audit log is append only, by grants and not by convention

The agent connects to Postgres as its own role, `deflect_app`. A migration grants that role `INSERT` and `SELECT` on `audit_log` and nothing else, so it cannot rewrite or remove a row even if the code tried. A trigger refuses `UPDATE` and `DELETE` for the owner too. Every tool call, every guardrail denial and every human decision writes a row, with personal details replaced by placeholders. A test logs in as that role and proves the `UPDATE`, `DELETE` and `TRUNCATE` all fail.

## Why the refund tool needs a policy id

`issue_refund` cannot be called without a `policy_doc_id`, and the server refuses any id that is not a real policy document. An ungrounded refund is impossible at the protocol level, not just discouraged by a prompt. Every write also carries an idempotency key, and the database's unique index, not Python code, is what refuses a repeat. See [mcp_server/SECURITY.md](mcp_server/SECURITY.md).

## What is in the repo right now

| Path | What it is |
| --- | --- |
| `agent/graph.py` | The LangGraph flow: redact, classify, retrieve, plan, guardrail, approval, act, draft, verify, escalate, respond. Checkpointed in Postgres. |
| `agent/nodes/guardrail.py` | Decides whether the one action the plan chose may run, pauses for a person, or denies it. |
| `agent/nodes/verify.py` | Checks each reply before it is sent: citations, the action that ran, claims of actions that never ran, refund timelines, then a model reads it against the sources. That model is its own setting and can be a chat model or a per sentence decider. |
| `agent/guardrails/policy.py` | The allowlist, the thresholds and the numeric policy rules, as data. |
| `agent/guardrails/audit.py` | The append only audit log writer. |
| `agent/approvals.py` | Approval requests, decided once, then the paused run resumes. |
| `agent/mcp_client.py` | The agent's only way to reach data and actions. 10 second timeout, one retry on transport errors only. |
| `agent/providers.py` | The only file that knows whether the model is Ollama, Gemini, OpenAI or Anthropic, and whether the reply checker and the classifier are one of those or Jev. |
| `agent/guardrails/redact.py` | Strips emails, phones, cards and house addresses before any model sees the ticket. |
| `agent/cli.py` | Runs one ticket from the terminal, approves or denies a paused one, can kill itself mid run and resume. |
| `api/main.py` | FastAPI: submit tickets, list pending approvals, approve or deny, and everything the console reads. Serves the built console. |
| `api/runview.py` | Builds the run view from the checkpoints and the audit log, with every string redacted again on the way out. |
| `api/tickets.py` | The inbox: one summary row per ticket, holding only the redacted message. |
| `api/limits.py` | A rate limit per caller and a daily model budget, in front of everything that can cost a model call. |
| `api/demo.py` | The ten tickets the public demo loads at startup, and its reset. |
| `console/` | The React console: inbox, run view, approval queue, metrics. |
| `Dockerfile`, `deploy/` | One image with the API, the console, Postgres and Qdrant, and the script that starts them. |
| `mcp_server/` | The standalone MCP server: nine tools, strict arguments, stdio and HTTP with bearer tokens. |
| `data/migrate.py` | Applies the SQL migrations, including the agent's role and the audit log grants. |
| `agent/tracing.py` | OpenTelemetry spans for every node, tool call and model call, redacted, exported to Langfuse, LangSmith or both. |
| `evals/run.py` | Runs the golden tickets, plays the approving person, writes every outcome to `results.json`. |
| `evals/metrics.py` | The ten headline metrics, per ticket category, plus the counts of fixed failures. |
| `evals/gate.py` | The CI gate. Exits non zero and names the metric when a threshold is breached, or when a stand in model classified or checked part of the run. |
| `evals/judge.py` | A hosted model scores each reply on accuracy, completeness, tone and restraint. |
| `evals/judge_agreement.py` | Compares the judge with 20 replies graded by hand. |
| `evals/compare.py` | Puts full runs on different models side by side. |
| `evals/stability.py` | Takes several runs of the same code and shows, for every ticket that must reach a person, how many of the runs sent it there and what decided it. |
| `evals/sources.py` | Reads back the order each ticket was about for a results file recorded before orders were kept in it. |
| `evals/checker_replay.py` | Runs a different reply checker over the drafts an earlier run recorded, in minutes, and shows where the two disagree. |
| `evals/classifier_replay.py` | Scores a different classifier against the 120 labels in about a minute, and shows which tickets it fixes and breaks compared with a recorded run. |
| `evals/components.py` | Scores every step of the graph on its own from a recorded run: classifier, retriever, planner, guardrail, generator, and the first step that went wrong on each lost ticket. |
| `evals/isolate.py` | Runs the retriever, the planner or the generator alone, with the steps before it replaced by the labels, and with policy excerpts from the labels or from the real search. |
| `evals/report.py` | Appends a row to each table in `docs/EVALS.md`. Never overwrites. |
| `evals/history.py` | Reads the tables in `docs/EVALS.md` back as data, for the console's metrics screen. |
| `.github/workflows/evals.yml` | Smoke eval and gate on every pull request, full suite and judge every night. |
| `docs/ARCHITECTURE.md` | How it fits together, what a trace holds, and Langfuse compared with LangSmith. |
| `docs/FAILURES.md` | Real failures from the eval runs, with causes, fixes and numbers. |
| `data/policies/` | 8 support policies. Their `doc_id` is what the agent cites. |
| `evals/golden/tickets.jsonl` | 120 hand labelled tickets the agent is measured against. |

## License

MIT
