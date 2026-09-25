# Evals

How Deflect is measured, and every result it has ever produced. Rows are appended by
`python -m evals.report` and never edited. A weak early row is what gives the later rows meaning.

## Version history

| Version | Change | Model | Cases | Intent acc | Esc. recall | Esc. precision | Grounded | Retrieval recall | Parse fail rate | Cost per ticket | Latency p50 | Latency p95 | Date |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 | Baseline, no tools | ollama qwen2.5:7b | 120 full | 0.88 | 0.93 | 0.34 | 1.00 | 0.88 | 0.00 | Rs 0.00 | 144.1 s | 248.2 s | 2026-09-25 |

## How to add a row

```bash
python -m data.seed_orders --reset
python -m data.index_policies
python -m evals.run --subset full --out results.json
python -m evals.report results.json --version v1 --change "Baseline, no tools"
```

## What each column means

| Column | Definition |
| --- | --- |
| Intent acc | Tickets whose predicted intent matches the label, out of all tickets. |
| Esc. recall | Of the tickets that must go to a human, the share the agent escalated. The safety number. |
| Esc. precision | Of the tickets the agent escalated, the share that really needed a human. |
| Grounded | Of the tickets the agent answered itself, the share that cite at least one policy and only cite policies that were actually retrieved. |
| Retrieval recall | Of the tickets that reached retrieval, the share where every required policy was in the top 5 results. If this is low, check the chunking first. |
| Parse fail rate | Structured output calls that failed validation, out of all structured output calls. Retries count as separate calls. |
| Cost per ticket | Mean model cost in rupees. Local Ollama runs are counted as zero. |
| Latency | End to end time per ticket, 50th and 95th percentile. |

A ticket that crashed counts as wrong in every column.

## Phase 1 notes

- The agent has no tools yet. Any ticket whose policy calls for an action is escalated with
  the reason `action_required`, so escalation recall is high and precision is low by design.
  Phase 2 adds the tools and should raise precision without lowering recall.
- Reply quality is not scored yet. The judge arrives in Phase 4.
