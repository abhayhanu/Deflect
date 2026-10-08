"""Grades the replies in a results file with a hosted model, on the rubric from the build spec.

The judge scores the reply only. Whether the right action ran is measured in code by metrics.py,
because a model grading its own family of model is not evidence.

The judge sees exactly five things: the customer's message, the policies retrieval returned,
the order record, the action that ran and the reply. It never sees the labels or the reference
reply, because a judge that can see the reference grades similarity to it instead of quality.
Personal details are redacted again before anything is sent, since the stored reply has the
real ones back in.

The order record was added after v6. Without it the judge had no way to tell a true statement
about the order from an invented one, and it scored "your order was cancelled and refunded" as
a hallucination on an order that had been cancelled and refunded. That is rubric 2. Scores from
the two rubrics are never mixed: a reply scored under the old one is judged again.

Escalations are skipped. Their reply is a fixed template the model never wrote.

Run it as a module with a results file. The scores are written back into that file, and the
record option also adds a row to the reply quality table in EVALS.md.
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from agent.guardrails.redact import redact
from agent.providers import Usage, call_structured, get_chat_model
from evals.metrics import compute
from evals.schema import GoldenCase
from evals.validate import GOLDEN_FILE, POLICY_DIR

log = logging.getLogger("evals.judge")

DIMENSIONS = ("accuracy", "completeness", "tone", "restraint")
JUDGED = {"answered", "acted"}

# From the build spec, with the order record named as a source and one sentence on a rule used
# for the wrong situation. Change it only together with a new judge validation.
RUBRIC_VERSION = 2
RUBRIC = """You are grading a customer support reply. You are given the customer's
message, the policy excerpts available, the order record from our
database, the action taken, and the reply.

Score each dimension 1-5. Output JSON only.

1. accuracy      Does every factual claim match the policy excerpts, the
                 order record and the action taken? A policy rule applied
                 to a situation it does not cover is not supported.
                 5 = all claims supported. 1 = contains a claim
                 contradicted by the excerpts or the order record.

2. completeness  Does it state what was done, the amount or timeline
                 where relevant, and what happens next? 5 = nothing a
                 customer would have to write back to ask.

3. tone          Appropriate to the sentiment given. Acknowledges
                 frustration without grovelling. 5 = a competent human
                 agent could have sent this. 1 = robotic or dismissive.

4. restraint     Does it avoid promising anything not in the excerpts
                 or the order record?
                 5 = promises only what policy supports. 1 = invents a
                 commitment ("we'll expedite this", "a manager will call").

Output: {"accuracy": n, "completeness": n, "tone": n, "restraint": n,
         "notes": "one sentence on the weakest dimension"}"""


class JudgeScore(BaseModel):
    accuracy: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    tone: int = Field(ge=1, le=5)
    restraint: int = Field(ge=1, le=5)
    notes: str


def golden_messages() -> dict[str, str]:
    lines = GOLDEN_FILE.read_text(encoding="utf-8").splitlines()
    return {c.case_id: c.raw_message for c in (GoldenCase.model_validate_json(x) for x in lines if x.strip())}


def policy_text(doc_ids: list[str]) -> str:
    """The full text of every policy retrieval returned, with each doc_id on top."""
    blocks = []
    for doc_id in doc_ids:
        path = POLICY_DIR / f"{doc_id}.md"
        if path.exists():
            body = path.read_text(encoding="utf-8").split("---", 2)[-1].strip()
            blocks.append(f"[doc_id: {doc_id}]\n{body}")
    return "\n\n".join(blocks) or "None."


def action_text(record: dict) -> str:
    done = [c for c in record.get("tool_calls", []) if not c["error"]]
    if not done:
        return "None. No action was taken."
    lines = []
    for call in done:
        args = {k: v for k, v in call["args"].items() if k != "idempotency_key"}
        lines.append(f"{call['name']} with {json.dumps(args)}\nResult: {json.dumps(call['result'])}")
    return "\n\n".join(lines)


def order_text(record: dict) -> str:
    """The order as the agent read it. It carries a city and no other personal detail, and is
    redacted like everything else on its way out."""
    order = record.get("order")
    if not order:
        return "None. No order was found for this ticket."
    return redact(json.dumps(order, default=str))[0]


def judge_prompt(record: dict, raw_message: str) -> list:
    """Built only from what the judge may see. The labels and the reference reply are never read."""
    body = f"""Customer message:
{redact(raw_message)[0]}

Policy excerpts available:
{policy_text(record["predicted"]["retrieved_ids"])}

Order record from our database:
{order_text(record)}

Action taken:
{action_text(record)}

Reply:
{redact(record["reply"] or "")[0]}"""
    return [SystemMessage(RUBRIC), HumanMessage(body)]


def should_judge(record: dict) -> str | None:
    """Returns why a record is skipped, or None when it should be judged."""
    if record["error"]:
        return "the run crashed"
    if record["predicted"]["terminal_reason"] not in JUDGED:
        return "escalation, the reply is a fixed template"
    if not record.get("reply"):
        return "no reply"
    return None


def judge_one(model, record: dict, raw_message: str) -> tuple[dict, Usage]:
    usage = Usage()
    try:
        score = call_structured(model, JudgeScore, judge_prompt(record, raw_message), usage)
    except Exception as exc:
        # One failed call must not throw away the scores already paid for.
        log.warning("Judge call failed for %s: %s", record["case_id"], exc)
        return {"failed": f"{type(exc).__name__}: {str(exc)[:200]}"}, usage
    if score is None:
        return {"failed": "the judge never returned valid scores"}, usage
    values = score.model_dump()
    values["mean"] = round(sum(values[d] for d in DIMENSIONS) / len(DIMENSIONS), 3)
    values["rubric"] = RUBRIC_VERSION
    return values, usage


def current(scores: dict | None) -> bool:
    """Whether a stored score was given under the rubric in use now. Files from before rubric 2
    carry no number, which means rubric 1."""
    return "accuracy" in (scores or {}) and scores.get("rubric", 1) == RUBRIC_VERSION


def judge_results(results: dict, model, subset: str | None = None, only: list[str] | None = None,
                  workers: int = 4, again: bool = False) -> dict:
    """Adds a judge entry to every chosen record and recomputes the metrics. Returns a summary."""
    messages = golden_messages()
    chosen = []
    for record in results["cases"]:
        if only and record["case_id"] not in only:
            continue
        if subset and subset not in record.get("tags", []):
            continue
        skip = should_judge(record)
        if skip:
            record["judge"] = {"skipped": skip}
        elif again or not current(record.get("judge")):
            if "accuracy" in (record.get("judge") or {}) and not current(record["judge"]):
                # Scores under an older rubric stay in the file, so the two can be compared.
                record.setdefault("judge_earlier", []).append(record["judge"])
            chosen.append(record)

    total = Usage()
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for record, (values, usage) in zip(chosen, pool.map(lambda r: judge_one(model, r, messages[r["case_id"]]), chosen)):
            record["judge"] = values
            total.cost_inr += usage.cost_inr
            total.input_tokens += usage.input_tokens
            total.output_tokens += usage.output_tokens
            shown = values.get("mean", values.get("failed"))
            print(f"  {record['case_id']}  {shown}  {values.get('notes', '')[:90]}")

    meta = results["meta"]
    same = model.provider == meta.get("provider") and model.name == meta.get("model")
    previous = results.get("judge") or {}
    if previous.get("rubric", 1) != RUBRIC_VERSION:
        previous = {}
    results["judge"] = {
        "provider": model.provider, "model": model.name, "same_model_as_agent": same, "rubric": RUBRIC_VERSION,
        "cost_inr": round(previous.get("cost_inr", 0.0) + total.cost_inr, 4),
        "input_tokens": previous.get("input_tokens", 0) + total.input_tokens,
        "output_tokens": previous.get("output_tokens", 0) + total.output_tokens,
    }
    results["metrics"] = compute(results["cases"])
    return results["judge"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Score the replies in a results file with a hosted judge model.")
    parser.add_argument("results", type=Path)
    parser.add_argument("--subset", choices=["smoke", "adversarial", "regression"], help="judge only these tickets")
    parser.add_argument("--case", action="append", help="judge only this case id, can be repeated")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--again", action="store_true", help="judge replies that already have scores")
    parser.add_argument("--record", metavar="VERSION", help="also append a row to the reply quality table in EVALS.md")
    parser.add_argument("--reseed", action="store_true",
                        help="reset the shop data to the anchor of the results file, to read back orders an older file lacks")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    try:
        model = get_chat_model("judge")
    except Exception as exc:
        print(f"Cannot start the judge: {exc}", file=sys.stderr)
        print("The judge needs a hosted model. Set DEFLECT_JUDGE_PROVIDER and its API key in .env, "
              "and run pip install -e .[hosted]", file=sys.stderr)
        return 2
    results = json.loads(args.results.read_text(encoding="utf-8"))
    from evals.sources import NotReady, attach_orders

    try:
        read_back = attach_orders(results, args.reseed)
    except NotReady as exc:
        print(exc, file=sys.stderr)
        return 2
    if read_back:
        print(f"Read back the order for {read_back} tickets and saved them in {args.results}")
    if model.provider == results["meta"].get("provider") and model.name == results["meta"].get("model"):
        print(f"Warning: the judge is {model.name}, the same model as the agent. It will share the agent's blind spots.",
              file=sys.stderr)

    print(f"Judging {args.results} with {model.provider}:{model.name}")
    summary = judge_results(results, model, args.subset, args.case, args.workers, args.again)
    args.results.write_text(json.dumps(results, indent=2), encoding="utf-8")

    m = results["metrics"]
    print(f"\n  judged replies       {m['judged_replies']}")
    for d in DIMENSIONS:
        print(f"  {d:<20} {m['judge_means'].get(d)}")
    print(f"  reply quality        {m['reply_quality']}")
    print(f"  judge cost           Rs {summary['cost_inr']:.2f}")
    print(f"\nWrote the scores into {args.results}")

    if args.record:
        from evals.report import QUALITY_START, append_row, build_quality_row, quality_key

        row = build_quality_row(args.record, results)
        append_row(Path(__file__).resolve().parent.parent / "docs" / "EVALS.md", quality_key(args.record, results), row,
                   QUALITY_START)
        print(f"Added to EVALS.md:\n{row}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
