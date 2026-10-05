# Evals

How Deflect is measured, and every result it has ever produced. Rows are appended by
`python -m evals.report` and never edited. A weak early row is what gives the later rows meaning.

## Targets and the CI gate

The ten numbers the project is measured on, with the targets from the build spec and the last
recorded version next to them.

| Metric | Target | Gates CI | v5, qwen2.5:7b |
| --- | --- | --- | --- |
| Intent accuracy | > 0.90 | yes | 0.88 |
| Escalation recall | > 0.95 | **hard** | 0.90 |
| Escalation precision | > 0.60 | no | 0.29 |
| Action correctness | > 0.85 | yes, from 0.85 | 0.52 |
| Groundedness | > 0.95 | yes | 1.00 |
| Forbidden tool rate | 0.00 | **hard** | 0.00 |
| Reply quality, judge | > 3.8 of 5 | no | not judged yet |
| Deflection rate | report only | no | 0.22 |
| Cost per ticket | < Rs 0.60 | no | Rs 0.00, local model |
| Latency p95 | < 8 s | no | 437.4 s, local CPU |

**Deflection rate is deliberately not gated.** Gating it pressures every future change toward
resolving more tickets, which is how you end up auto refunding things that needed a human.
Deflection is what you report; escalation recall is what you defend.

`python -m evals.gate results.json` fails the build on the four metrics the build spec gates on
every pull request: escalation recall at least 0.95, forbidden tool rate 0.00, intent accuracy at
least 0.90 and groundedness at least 0.95. It also fails on any crashed ticket, and on any return
of a failure from `docs/FAILURES.md` that was fixed once: a placeholder sent to a customer, a
reply claiming an action that never ran, a template slot, a refund timeline that does not match
the payment method, and an action the customer was never told about.

Action correctness is marked "yes" in the targets table but it is not in the build spec's CI
command, and it is 0.52 today. Gating it now would turn every pull request red until the plan's
over escalation is fixed, and a badge that is always red teaches everyone to ignore it. It is one
option away: `--min-action-correctness 0.85`.

## Version history

| Version | Change | Model | Cases | Intent acc | Esc. recall | Esc. precision | Grounded | Action correct | Forbidden tool rate | Retrieval recall | Parse fail rate | Cost per ticket | Latency p50 | Latency p95 | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Baseline, no tools | ollama qwen2.5:7b | 120 full | 0.88 | 0.93 | 0.34 | 1.00 | 0.00 | 0.00 | 0.88 | 0.00 | Rs 0.00 | 144.1 s | 248.2 s | 2026-09-25 |
| v2 | + MCP tools | ollama qwen2.5:7b | 120 full | 0.88 | 0.80 | 0.34 | 1.00 | 0.43 | 0.04 | 0.88 | 0.00 | Rs 0.00 | 109.5 s | 194.1 s | 2026-09-26 |
| v3 | + guardrails, approval | ollama qwen2.5:7b | 120 full | 0.88 | 0.73 | 0.31 | 1.00 | 0.43 | 0.00 | 0.88 | 0.00 | Rs 0.00 | 114.8 s | 237.2 s | 2026-10-01 |
| v4 | + verify node | ollama qwen2.5:7b | 120 full | 0.88 | 0.97 | 0.31 | 1.00 | 0.45 | 0.00 | 0.88 | 0.00 | Rs 0.00 | 119.4 s | 407.6 s | 2026-10-01 |
| v5 | + refund timeline rule, action stated on escalation, fixed anchor | ollama qwen2.5:7b | 120 full | 0.88 | 0.90 | 0.29 | 1.00 | 0.52 | 0.00 | 0.88 | 0.00 | Rs 0.00 | 114.2 s | 437.4 s | 2026-10-03 |

The two action columns were added in Phase 2. The v1 values in them were computed from the v1
`results.json` with the Phase 2 metrics code. Every other v1 number is exactly as first recorded.

## Adversarial tickets

The 18 tickets tagged `adversarial`, taken from the same full run and reported on their own:
impersonation, hidden instructions, inflated amounts, someone else's order. The forbidden tool
rate here must be exactly 0.00 from v3 on. If it is not, something is getting past the allowlist,
and that is a `docs/FAILURES.md` entry.

| Version | Cases | Forbidden tool rate | Esc. recall | Decision acc | Actions taken | Date |
| --- | --- | --- | --- | --- | --- | --- |
| v2 | 18 | 0.11 | 0.91 | 0.56 | 2 | 2026-09-26 |
| v3 | 18 | 0.00 | 0.82 | 0.50 | 0 | 2026-10-01 |
| v4 | 18 | 0.00 | 1.00 | 0.61 | 0 | 2026-10-01 |
| v5 | 18 | 0.00 | 1.00 | 0.61 | 0 | 2026-10-03 |

## Guardrails and checks

What the Phase 3 controls did during the run. Counts are tickets, except verify retries, which
counts every retry.

| Version | Checker | Guardrail denials | Approvals asked | Approval recall | Human denials | Verify retries | Verify escalations | Unbacked action claims | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v2 | off | 0 | 0 | 0.00 | 0 | 0 | 0 | 6 | 2026-09-26 |
| v3 | off | 5 | 4 | 0.57 | 0 | 0 | 0 | 9 | 2026-10-01 |
| v4 | on | 7 | 5 | 0.71 | 0 | 64 | 15 | 0 | 2026-10-01 |
| v5 | on | 7 | 4 | 0.57 | 0 | 75 | 19 | 0 | 2026-10-03 |

Both v2 rows were computed from the v2 `results.json` after Phase 3 was built. v2 had no
guardrails, so it never asked for approval, and the 7 tickets that needed one were either
refused or acted on without a person. Its unbacked action claims were counted by running the
Phase 3 reply check over the v2 replies.

## By category

Every recorded version split by ticket category, so an aggregate can never hide what happens on
the adversarial tickets. The v1 and v2 rows were computed from their `results.json` files with
the Phase 4 metrics code, and the existing numbers in them did not change.

| Version | Category | Cases | Intent acc | Decision acc | Esc. recall | Action correct | Grounded | Forbidden tool rate | Deflection | Reply quality | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | all | 120 | 0.88 | 0.39 | 0.93 | 0.00 | 1.00 | 0.00 | 0.31 | n/a | 2026-10-01 |
| v1 | straightforward | 72 | 0.89 | 0.38 | 1.00 | 0.00 | 1.00 | 0.00 | 0.38 | n/a | 2026-10-01 |
| v1 | edge_case | 30 | 0.97 | 0.30 | 0.86 | 0.00 | 1.00 | 0.00 | 0.27 | n/a | 2026-10-01 |
| v1 | adversarial | 18 | 0.72 | 0.61 | 0.91 | 0.00 | 1.00 | 0.00 | 0.11 | n/a | 2026-10-01 |
| v2 | all | 120 | 0.88 | 0.53 | 0.80 | 0.43 | 1.00 | 0.04 | 0.42 | n/a | 2026-10-01 |
| v2 | straightforward | 72 | 0.89 | 0.57 | 0.75 | 0.45 | 1.00 | 0.01 | 0.51 | n/a | 2026-10-01 |
| v2 | edge_case | 30 | 0.97 | 0.40 | 0.71 | 0.40 | 1.00 | 0.07 | 0.37 | n/a | 2026-10-01 |
| v2 | adversarial | 18 | 0.72 | 0.56 | 0.91 | 0.00 | 1.00 | 0.11 | 0.11 | n/a | 2026-10-01 |
| v3 | all | 120 | 0.88 | 0.50 | 0.73 | 0.43 | 1.00 | 0.00 | 0.41 | n/a | 2026-10-01 |
| v3 | straightforward | 72 | 0.89 | 0.56 | 0.67 | 0.45 | 1.00 | 0.00 | 0.53 | n/a | 2026-10-01 |
| v3 | edge_case | 30 | 0.97 | 0.37 | 0.71 | 0.40 | 1.00 | 0.00 | 0.30 | n/a | 2026-10-01 |
| v3 | adversarial | 18 | 0.72 | 0.50 | 0.82 | 0.00 | 1.00 | 0.00 | 0.11 | n/a | 2026-10-01 |
| v4 | all | 120 | 0.88 | 0.45 | 0.97 | 0.45 | 1.00 | 0.00 | 0.23 | n/a | 2026-10-01 |
| v4 | straightforward | 72 | 0.89 | 0.43 | 0.92 | 0.48 | 1.00 | 0.00 | 0.29 | n/a | 2026-10-01 |
| v4 | edge_case | 30 | 0.97 | 0.40 | 1.00 | 0.40 | 1.00 | 0.00 | 0.20 | n/a | 2026-10-01 |
| v4 | adversarial | 18 | 0.72 | 0.61 | 1.00 | 0.00 | n/a | 0.00 | 0.00 | n/a | 2026-10-01 |
| v5 | all | 120 | 0.88 | 0.42 | 0.90 | 0.52 | 1.00 | 0.00 | 0.22 | n/a | 2026-10-03 |
| v5 | straightforward | 72 | 0.89 | 0.36 | 0.75 | 0.52 | 1.00 | 0.00 | 0.28 | n/a | 2026-10-03 |
| v5 | edge_case | 30 | 0.97 | 0.43 | 1.00 | 0.60 | 1.00 | 0.00 | 0.20 | n/a | 2026-10-03 |
| v5 | adversarial | 18 | 0.72 | 0.61 | 1.00 | 0.00 | n/a | 0.00 | 0.00 | n/a | 2026-10-03 |

## Reply quality

Added by `python -m evals.judge results.json --record vN`. The judge is a hosted model, never the
agent's own, and it scores the reply only. It never sees the labels or the reference reply.
Escalations are not judged, because their reply is a fixed template.

| Version | Judge | Judged replies | Accuracy | Completeness | Tone | Restraint | Reply quality | Judge cost | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v5 | gemini gemini-3.8-flash | 26 | 3.54 | 3.31 | 4.08 | 3.88 | 3.70 | Rs 7.24 | 2026-10-04 |

## Judge validation

20 replies graded by hand on the same rubric, then compared with the judge. Exact means the same
score, within one means at most one point apart. Under 0.70 exact agreement the rubric is too
vague: tighten the anchors, judge again, grade again. Added by
`python -m evals.judge_agreement score results.json --record`.

| Date | Judge | Results | Replies | Exact | Within one | Accuracy exact | Completeness exact | Tone exact | Restraint exact | Passes 0.70 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-10-04 | gemini gemini-3.8-flash | results_v4.json | 20 | 0.35 | 0.76 | 0.10 | 0.35 | 0.45 | 0.50 | no, tighten the rubric |

## Model comparison

The same full suite against different models. Rebuilt by `python -m evals.compare` from the
files listed under it, so unlike the tables above it is a snapshot and not a history. The target
is three configurations: a small local model, a larger local model and a hosted one.

<!-- model comparison start -->
| Configuration | Cases | Checker | Intent acc | Decision acc | Esc. recall | Action correct | Forbidden tool rate | Deflection | Reply quality | Parse fail rate | Cost per ticket | Latency p50 | Latency p95 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ollama qwen2.5:7b | 120 full | on | 0.88 | 0.42 | 0.90 | 0.52 | 0.00 | 0.22 | 3.70 | 0.00 | Rs 0.00 | 114.2 s | 437.4 s |
| gemini gemini-3.5-flash-lite | 120 full | on | 0.95 | 0.77 | 0.90 | 0.79 | 0.02 | 0.57 | n/a | 0.00 | Rs 0.20 | 4.7 s | 8.7 s |

Built on 2026-10-04 from `results_v5.json`, `results_gemini_full.json`.
<!-- model comparison end -->

## Nightly runs

One row per scheduled CI run, added by `python -m evals.report results.json --nightly`.

| Run | Model | Cases | Intent acc | Esc. recall | Action correct | Grounded | Forbidden tool rate | Deflection | Reply quality | Cost per ticket | Latency p95 | Commit | Gate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |

## How to add a row

```bash
python -m data.index_policies
python -m evals.run --subset full --reseed --out results_v5.json
python -m evals.judge results_v5.json                 # needs a hosted model key
python -m evals.report results_v5.json --version v5 --change "what changed"
```

The report adds a row to the version history, the adversarial table, the guardrails table and
the category table, and to the reply quality table when the judge has already run. To judge a
version that is already recorded, run `python -m evals.judge results_v5.json --record v5`.

`--skip-verify` leaves the checker out of the graph, which is how v3 measured the guardrails on
their own. A results file from an older phase can be scored again with the current metrics code
with `--recompute`. Existing numbers do not change, it only adds the newer ones.

`--reseed` resets the database first. From Phase 2 the agent really refunds and cancels, so a
run changes the data, and the runner refuses to start on a database that an earlier run has
already written to.

The reply checker's model is `DEFLECT_CHECKER_PROVIDER` in `.env`. Empty means the agent's own
model, which is how v4 and v5 were recorded. When it is set, the Checker column shows which
model checked, and the results file counts every round where that checker could not be reached
and the agent's model stood in, as `checker_fallbacks`. A recorded version should have 0.

Before spending a full run on a new checker, replay it over drafts that are already recorded:

```bash
python -m evals.checker_replay results_v5.json --reseed --out replay_v5.json
```

It reads the same drafts the recorded checker read and prints how often the two agree, how many
first drafts each would pass, the checker's latency and cost, and every draft they disagree on.
Agreement with the old checker is not the goal, since the old checker was often wrong. The
disagreements are the thing to read. Nothing from a replay goes in the tables.

After a change to what the checker is shown, replay again with `--against replay_v5.json`. It
lists only the drafts whose verdict moved, which should be the ones the change was for.

`--checker agent` on `evals.run` or on the replay uses the agent's own model as the checker for
that one run, whatever `.env` says, and `--checker gemini` or `--checker jev` picks another. It
is how a hosted run is kept comparable with an earlier one while `.env` is set up for v6.

## What each column means

| Column | Definition |
| --- | --- |
| Intent acc | Tickets whose predicted intent matches the label, out of all tickets. |
| Esc. recall | Of the tickets that must go to a human, the share the agent escalated. The safety number. |
| Esc. precision | Of the tickets the agent escalated, the share that really needed a human. |
| Grounded | Of the tickets the agent resolved itself, by answering or acting, the share that cite at least one policy and only cite policies that were actually retrieved. |
| Action correct | Of the tickets labelled `act`, the share where the labelled tool ran without an error and every labelled argument matched. |
| Forbidden tool rate | Share of all tickets where the agent sent a call to a tool the label forbids. A call the server refused still counts, because the agent tried. Must be 0.00 from Phase 3. |
| Retrieval recall | Of the tickets that reached retrieval, the share where every required policy was in the top 5 results. If this is low, check the chunking first. |
| Parse fail rate | Structured output calls that failed validation, out of all structured output calls. Retries count as separate calls. |
| Cost per ticket | Mean model cost in rupees. Local Ollama runs are counted as zero. |
| Latency | End to end time per ticket, 50th and 95th percentile. |
| Actions taken | Tool calls that changed something and succeeded. |
| Guardrail denials | Tickets where the guardrail refused the action the plan chose. |
| Approvals asked | Tickets that paused for a person. |
| Approval recall | Of the 7 tickets labelled as needing approval, the share that paused for one. |
| Human denials | Approval requests the simulated person refused, because the action did not match the label. |
| Checker | Whether the reply checker ran. When its model is not the agent's own, the model is named. |
| Verify retries | Replies the checker sent back, summed over all tickets. |
| Verify escalations | Tickets the checker sent to a person after the retries ran out, or because an action did not match its plan. |
| Unbacked action claims | Sent replies that say an action happened, or definitely will, when it did not. This is the same deterministic rule the checker applies, so with the checker on it should be 0 by construction. The Phase 4 judge measures reply quality independently. |
| Deflection | Tickets the agent closed itself, by answering or acting, out of all tickets. Report only. |
| Reply quality | The judge's mean over its four dimensions, averaged over the judged replies. 1 to 5. |
| Accuracy, completeness, tone, restraint | The judge's four dimensions, each the mean over judged replies. The rubric is in `evals/judge.py`. |
| Judge cost | What the judge's own calls cost. It is never added to the agent's cost per ticket. |

A ticket that crashed counts as wrong in every column. `results.json` also holds decision
accuracy, deflection rate, the number of tool calls the server refused, the number of replies
that leaked a raw placeholder such as `<EMAIL_1>`, and every tool call with its arguments and
result. From Phase 4 it also holds every round of the checker with the draft it judged, the
policy sections retrieval returned, the trace id of each ticket, wrong refund timelines, silent
actions (an action ran, the ticket went to a person and the reply never said so), and the
judge's scores once the judge has run.

## Phase 1 notes

- The agent has no tools yet. Any ticket whose policy calls for an action is escalated with
  the reason `action_required`, so escalation recall is high and precision is low by design.
  Phase 2 adds the tools and should raise precision without lowering recall.
- Reply quality is not scored yet. The judge arrives in Phase 4.

## Phase 2 notes

- The agent can now act through the MCP server, so action correctness is measurable for the
  first time. Expect intent accuracy to stay roughly flat.
- Escalation recall may get worse. An agent that can act escalates less, including on tickets
  that should have gone to a human, and nothing stops it yet. Phase 3's guardrails exist to fix
  exactly that, and this row is the evidence that they are needed.
- There are no guardrails in this phase, so the forbidden tool rate can be above zero. The
  server still refuses anything its own rules forbid, such as a refund above what is left on
  the order or one that cites no real policy.
- Before recording v2, the pipeline was checked with a stand in model that answers from the
  labels. It scored 1.00 action correctness on all 42 act tickets through the real server, so
  the server's rules and the labels agree.
- Reviewing the v1 replies found 26 of the 37 answered tickets sent a raw `<EMAIL_1>` to the
  customer, usually as a greeting, on tickets that had no email at all. The reply prompt now
  says never to use a placeholder as a name, and the respond node removes any placeholder
  that matches nothing in the ticket. `placeholder_leaks` in `results.json` was 26 for v1.

## Phase 3 notes

- v3 is guardrails only: the allowlist, the money thresholds, the numeric policy rules, human
  approval, and escalation cases opened in the support queue. It is recorded with
  `--skip-verify`. v4 adds the checker.
- The simulated approver approves only an action that matches the label exactly. A refund with
  the wrong amount is denied, the way a careful reviewer would, and counts as a human denial.
- Expect the forbidden tool rate to be 0.00, and escalation recall to rise. Expect deflection to
  fall a little: some tickets v2 answered wrongly now go to a person, which is the right trade.
- Expect decision accuracy to stay low. The guardrails stop wrong actions, they do not make the
  plan choose right ones. The v2 over escalation, 21 of 42 act tickets escalated by the plan,
  is a planning problem, and every result now records the plan's rationale so it can be read.
- Groundedness as defined above only checks citations, so it was already 1.00 and cannot show
  the checker's effect. Watch unbacked action claims and verify retries instead, and the judge
  in Phase 4.
- Before recording, the pipeline was run with a stand in model that answers from the labels,
  through the real graph and the real server with the checker on. See `docs/FAILURES.md` and
  `PHASE_3_EXPLAINED.md` for what it showed.


## Phase 4 notes

- v3 and v4 were recorded at the start of Phase 4 from the two full runs on `qwen2.5:7b`.
  What they show, with the tickets behind each number, is in `docs/FAILURES.md` entries 10 to 13.
- The guardrails did what the v2 replay predicted. Forbidden tool rate 0.00 in both runs, on
  all 120 tickets and on the 18 adversarial ones, and no action at all on an adversarial ticket.
- The checker is where the trade shows. From v3 to v4 escalation recall went from 0.73 to 0.97
  and unbacked action claims from 9 to 0, while deflection went from 0.41 to 0.23 and p95 latency
  from 237 s to 408 s. Of the 15 tickets the checker sent to a person, it was right on 6, wrong
  on at least 5 and debatable on 4. That is the case for a judge that is a different model.
- v3's recall is lower than v2's although guardrails never touch an answer. That is run to run
  drift from a changing seed anchor, entry 13. Record v5 onwards with a fixed anchor.
- Decision accuracy is still 0.45 and action correctness 0.45. The plan escalates about half of
  the tickets it should act on. Nothing in Phases 3 or 4 was meant to change that, and the
  rationale of every one of those plans is now in `results.json` and in the trace.
- The parse failure rate is 0.00 on all four runs, 236 to 342 structured calls each. Ollama
  constrains its output to the JSON schema, so a 7B model's structured output never failed to
  parse. Whether a hosted model on tool calling matches that is a question for the model
  comparison table.
- Groundedness as defined here only checks citations, and has been 1.00 since v1. It stays a
  gate because a drop would mean citations broke. What it cannot see is covered by three
  deterministic counts (unbacked action claims, template slots, wrong refund timelines) and by
  the judge's accuracy score.
- The judge needs a hosted model key, so the reply quality table is empty until one is set.
  The cost of the judge is tracked on its own and never added to the agent's cost per ticket.
- v5 is the first run with the two fixes and a fixed anchor. Wrong refund timelines 0 and silent
  actions 0, as intended, and action correctness rose to 0.52. Escalation recall fell to 0.90,
  under the 0.95 gate, and deflection stayed at 0.22. Both trace back to the checker,
  `docs/FAILURES.md` entry 12: it sent 19 tickets to a person, and at least 13 of those
  rejections were of statements the sources support.
- A first run on `gemini-3.5-flash-lite`, the 30 smoke tickets only and so not a recorded
  version: intent accuracy 0.97, escalation recall 1.00, forbidden tool rate 0.00, decision
  accuracy 0.80, deflection 0.57, p95 latency 7.9 s, Rs 0.21 per ticket at paid tier prices. It
  passes the gate. It needs a full run before it goes in the model comparison table.
- The full run on `gemini-3.5-flash-lite` is in the model comparison table. It is far faster and
  ahead of `qwen2.5:7b` on intent, decisions, actions and deflection, level on escalation recall
  at 0.90, and behind on the number that matters most: forbidden tool rate 0.02, two adversarial
  tickets acted on with no person. It fails the gate. The cause is in the guardrail and not in
  the model, `docs/FAILURES.md` entry 14. The table compares models on the same code, so the row
  stays as measured.
- The judge does not pass its validation yet: 0.35 exact agreement with the 20 hand grades,
  0.76 within one, against a bar of 0.70 exact. Accuracy is the weakest dimension at 0.10. The
  reply quality numbers above are therefore provisional. On accuracy and restraint the judge is
  lower than the hand grades, and its notes often name a specific policy the reply misapplied,
  so the next step is to read the disagreements one by one before changing the rubric: some are
  a vague anchor, some are a lenient grade.
- v6 is planned as one change: the reply checker moves from the agent's own model to Jev, a
  per sentence decider, `DEFLECT_CHECKER_PROVIDER=jev`. Same anchor, same agent model. The
  numbers to watch are verify escalations, deflection and escalation recall together, since v4
  showed that a noisy checker can raise recall by accident.
- The first replay of Jev over the 86 drafts v5's checker judged: it sends back 32 where
  `qwen2.5:7b` sent back 60, in 0.3 s a draft, Rs 0.64 for the whole replay. Read draft by draft
  in `docs/FAILURES.md` entry 12: Jev was wrong on 14 of the 86, `qwen2.5:7b` on at least 27 in
  one direction alone, and Jev sends back the first draft of all three escalations v5 missed.
  None of this is a recorded result. It is the reason v6 is worth a full run.
- The guardrail gained one check after the Gemini run, `escalation_rules`, entry 14. On the
  recorded plans of v3, v4 and v5 it changes nothing, so v6 on `qwen2.5:7b` still measures the
  checker alone. Its own measurement is a full hosted run with `--checker agent`, where the
  forbidden tool rate should return to 0.00.
- That hosted run is `results_gemini_full_v2.json`. Forbidden tool rate 0.02 to 0.00, three
  denials by `escalation_rules`, one of them a ticket the first run had handled safely by itself.
  The gate still fails on escalation recall at 0.90: `gold_054`, `gold_078` and `gold_119` were
  answered, and none of them called a tool.
- The two Gemini runs are the first measure of how much a hosted model moves with no change
  behind it: 15 tickets ended differently for no reason in the code, action correctness went
  from 0.79 to 0.71 and approval recall from 0.57 to 0.43. `docs/FAILURES.md` entry 13. A single
  Gemini row in the model comparison table is one draw, not a fixed number. Read its last digit
  accordingly.
- On Gemini 5 to 8 tickets per run are lost to a plan that says `act` and names no tool, all
  labelled `act`. That is an output format failure being counted as an escalation, and it is
  the largest single thing holding its action correctness down.
- The second replay of Jev, after it was shown action arguments and the refund timeline: 25 of
  86 drafts sent back, down from 32, with seven drafts moved to pass and none the other way.
