"""Checks the golden dataset against the policy documents and the seed database.

This runs before any agent code exists. If it fails, the labels are wrong or point at data
that does not exist, and every metric built on top of them would be meaningless.
"""

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml
from pydantic import ValidationError

from evals.schema import (
    ACTION_TOOLS,
    CATEGORY_TAGS,
    INTENTS,
    REFUND_REASON_CODES,
    TOOL_ALLOWLIST,
    TOOL_CLASSES,
    GoldenCase,
)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_FILE = ROOT / "evals" / "golden" / "tickets.jsonl"
POLICY_DIR = ROOT / "data" / "policies"

EXPECTED_POLICY_COUNT = 8
TARGET_MIX = {"straightforward": 0.60, "edge_case": 0.25, "adversarial": 0.15}
MIX_TOLERANCE = 0.05
MIN_ESCALATIONS = 15
SMOKE_SIZE = 30
AUTO_APPROVE_CEILING_INR = 3_000
HARD_REFUND_CEILING_INR = 25_000
STALE_SEED_AFTER = timedelta(hours=24)
ORDER_TOKEN = re.compile(r"\bA\d{4,6}\b")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, where: str, message: str) -> None:
        self.errors.append(f"{where:<10} {message}")

    def warn(self, where: str, message: str) -> None:
        self.warnings.append(f"{where:<10} {message}")


@dataclass
class Seed:
    customers: set[str]
    orders: dict[str, dict]
    anchor: datetime | None
    source: str


def load_policies(directory: Path, report: Report) -> dict[str, dict]:
    policies = {}
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        parts = text.split("---", 2)
        if not text.startswith("---") or len(parts) < 3:
            report.error(path.name, "missing YAML frontmatter")
            continue

        meta = yaml.safe_load(parts[1]) or {}
        doc_id = meta.get("doc_id")
        missing = [k for k in ("doc_id", "title", "applies_to", "version") if k not in meta]
        if missing:
            report.error(path.name, f"frontmatter is missing {', '.join(missing)}")
            continue
        if doc_id in policies:
            report.error(path.name, f"duplicate doc_id {doc_id}")
            continue
        if path.stem != doc_id:
            report.error(path.name, f"file name should match doc_id {doc_id}")

        unknown = set(meta["applies_to"]) - set(INTENTS)
        if unknown:
            report.error(doc_id, f"applies_to has unknown intents {sorted(unknown)}")
        if not re.search(r"\d", parts[2]):
            report.error(doc_id, "has no numeric rule, so groundedness cannot be scored against it")

        policies[doc_id] = meta

    if len(policies) != EXPECTED_POLICY_COUNT:
        report.error("policies", f"expected {EXPECTED_POLICY_COUNT} policy documents, found {len(policies)}")
    return policies


def load_cases(path: Path, report: Report) -> list[GoldenCase]:
    cases = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        where = f"line {line_no}"
        try:
            cases.append(GoldenCase.model_validate(json.loads(line)))
        except json.JSONDecodeError as exc:
            report.error(where, f"invalid JSON: {exc.msg}")
        except ValidationError as exc:
            for err in exc.errors():
                field_path = ".".join(str(p) for p in err["loc"])
                report.error(where, f"{field_path}: {err['msg']}")
    return cases


def load_seed_from_db() -> Seed:
    from data.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT customer_id FROM customers")
        customers = {row[0] for row in cur.fetchall()}
        cur.execute("SELECT order_id, customer_id, total_inr, refunded_inr FROM orders")
        orders = {
            oid: {"customer_id": cid, "total_inr": float(total), "refunded_inr": float(refunded)}
            for oid, cid, total, refunded in cur.fetchall()
        }
        cur.execute("SELECT value FROM seed_meta WHERE key = 'anchor'")
        row = cur.fetchone()
    anchor = datetime.fromisoformat(row[0]) if row else None
    return Seed(customers, orders, anchor, "database")


def load_seed_offline() -> Seed:
    from data.seed_orders import build_dataset

    anchor = datetime.now(timezone.utc)
    data = build_dataset(anchor)
    orders = {
        o["order_id"]: {"customer_id": o["customer_id"], "total_inr": o["total_inr"], "refunded_inr": o["refunded_inr"]}
        for o in data["orders"]
    }
    return Seed({c["customer_id"] for c in data["customers"]}, orders, anchor, "built in memory")


def check_order_links(case: GoldenCase, seed: Seed, report: Report) -> None:
    cid = case.case_id
    tags = set(case.tags)

    if case.order_id:
        order = seed.orders.get(case.order_id)
        if order is None:
            report.error(cid, f"order {case.order_id} does not exist in the seed data")
        elif "other_customer_order" in tags:
            if order["customer_id"] == case.customer_id:
                report.error(cid, "tagged other_customer_order but the order belongs to this customer")
        elif order["customer_id"] != case.customer_id:
            report.error(cid, f"order {case.order_id} belongs to {order['customer_id']}, not {case.customer_id}")
        if case.order_id not in case.raw_message:
            report.error(cid, f"order_id {case.order_id} is not mentioned in raw_message")

    mentioned = set(ORDER_TOKEN.findall(case.raw_message))
    unknown = sorted(m for m in mentioned if m not in seed.orders)
    if "nonexistent_order" in tags:
        if not unknown:
            report.error(cid, "tagged nonexistent_order but every order it mentions exists")
    elif unknown:
        report.error(cid, f"mentions orders missing from the seed data: {', '.join(unknown)}")


def check_refund_args(case: GoldenCase, seed: Seed, policies: dict, report: Report) -> None:
    cid = case.case_id
    exp = case.expected
    args = exp.tool_args or {}

    amount = args.get("amount_inr")
    if not isinstance(amount, (int, float)) or amount <= 0:
        report.error(cid, "issue_refund needs a positive amount_inr")
        return

    order = seed.orders.get(args.get("order_id"))
    if order:
        remaining = order["total_inr"] - order["refunded_inr"]
        if amount > remaining:
            report.error(cid, f"refund of Rs {amount:,} exceeds what is left on the order (Rs {remaining:,.0f})")
    if amount > HARD_REFUND_CEILING_INR:
        report.error(cid, f"refund of Rs {amount:,} is above the hard ceiling, it should be an escalation")
    if args.get("reason_code") not in REFUND_REASON_CODES:
        report.error(cid, f"unknown reason_code {args.get('reason_code')!r}")

    policy_id = args.get("policy_doc_id")
    if policy_id not in policies:
        report.error(cid, f"policy_doc_id {policy_id!r} is not a real policy document")
    elif policy_id not in exp.required_policy_ids:
        report.error(cid, f"policy_doc_id {policy_id} is not listed in required_policy_ids")

    needs_approval = amount > AUTO_APPROVE_CEILING_INR
    if exp.requires_approval != needs_approval:
        report.error(cid, f"requires_approval should be {needs_approval} for Rs {amount:,}")


def check_case(case: GoldenCase, seed: Seed, policies: dict, report: Report) -> None:
    cid = case.case_id
    exp = case.expected

    if case.customer_id not in seed.customers:
        report.error(cid, f"customer {case.customer_id} does not exist in the seed data")
    check_order_links(case, seed, report)

    if case.category is None:
        report.error(cid, f"needs exactly one of the tags {', '.join(CATEGORY_TAGS)}")

    for pid in exp.required_policy_ids:
        if pid not in policies:
            report.error(cid, f"required policy {pid} does not exist")
        elif exp.intent not in policies[pid]["applies_to"]:
            report.error(cid, f"{pid} is not retrievable for intent {exp.intent}, check its applies_to")
    if not exp.required_policy_ids and exp.intent != "out_of_scope":
        report.error(cid, "required_policy_ids is empty, only out_of_scope cases may skip policies")

    if exp.must_escalate and exp.decision != "escalate":
        report.error(cid, "must_escalate is true but decision is not escalate")
    if exp.decision == "escalate" and not exp.must_escalate:
        report.error(cid, "decision is escalate but must_escalate is false")

    unknown_tools = [t for t in exp.forbidden_tools if t not in TOOL_CLASSES]
    if unknown_tools:
        report.error(cid, f"forbidden_tools has unknown tools {unknown_tools}")

    if exp.decision != "act":
        if exp.tool_name or exp.tool_args or exp.requires_approval:
            report.error(cid, "only act cases may set tool_name, tool_args or requires_approval")
        return

    tool = exp.tool_name
    if tool not in ACTION_TOOLS:
        report.error(cid, f"act case needs one of {sorted(ACTION_TOOLS)}, got {tool!r}")
        return
    if tool not in TOOL_ALLOWLIST[exp.intent]:
        report.error(cid, f"{tool} is not allowed for intent {exp.intent}, the case can never pass")
    if tool in exp.forbidden_tools:
        report.error(cid, f"{tool} is both the expected tool and a forbidden tool")
    if not exp.tool_args or exp.tool_args.get("order_id") != case.order_id:
        report.error(cid, "tool_args.order_id must match the case order_id")
    if tool == "issue_refund":
        check_refund_args(case, seed, policies, report)
    elif exp.requires_approval:
        report.error(cid, f"requires_approval only applies to issue_refund, not {tool}")


def check_dataset(cases: list[GoldenCase], report: Report) -> None:
    ids = Counter(c.case_id for c in cases)
    for case_id, count in ids.items():
        if count > 1:
            report.error(case_id, f"case_id is used {count} times")

    escalations = sum(c.expected.must_escalate for c in cases)
    if escalations < MIN_ESCALATIONS:
        report.error("dataset", f"only {escalations} must_escalate cases, need at least {MIN_ESCALATIONS}")

    total = len(cases) or 1
    mix = Counter(c.category for c in cases)
    for tag, target in TARGET_MIX.items():
        share = mix[tag] / total
        if abs(share - target) > MIX_TOLERANCE:
            report.error("dataset", f"{tag} is {share:.0%} of cases, target is {target:.0%} within {MIX_TOLERANCE:.0%}")

    if not 110 <= len(cases) <= 130:
        report.warn("dataset", f"{len(cases)} cases, the brief asks for about 120")

    smoke = [c for c in cases if "smoke" in c.tags]
    if len(smoke) != SMOKE_SIZE:
        report.warn("smoke", f"smoke subset has {len(smoke)} cases, CI expects {SMOKE_SIZE}")
    if smoke and not all(any(c.category == tag for c in smoke) for tag in CATEGORY_TAGS):
        report.warn("smoke", "smoke subset should include every category")


def check_seed_freshness(seed: Seed, report: Report) -> None:
    if seed.anchor is None:
        report.warn("seed", "no anchor recorded, run python -m data.seed_orders")
    elif datetime.now(timezone.utc) - seed.anchor > STALE_SEED_AFTER:
        report.warn(
            "seed",
            f"seeded at {seed.anchor:%Y-%m-%d %H:%M} UTC. Time based labels assume a fresh seed, "
            "so reseed with the reset option before an eval run",
        )


def validate(golden: Path, policy_dir: Path, seed: Seed) -> tuple[Report, list[GoldenCase], dict]:
    report = Report()
    policies = load_policies(policy_dir, report)
    cases = load_cases(golden, report)
    for case in cases:
        check_case(case, seed, policies, report)
    check_dataset(cases, report)
    check_seed_freshness(seed, report)
    return report, cases, policies


def print_summary(report: Report, cases: list[GoldenCase], policies: dict, seed: Seed) -> None:
    total = len(cases) or 1
    mix = Counter(c.category for c in cases)
    intents = Counter(c.expected.intent for c in cases)
    decisions = Counter(c.expected.decision for c in cases)

    print("Deflect golden dataset check\n")
    print(f"  Policies     {len(policies)} documents")
    print(f"  Seed data    {len(seed.customers)} customers, {len(seed.orders)} orders ({seed.source})")
    print(f"  Cases        {len(cases)}")
    print(f"  Mix          " + "  ".join(f"{t} {mix[t]} ({mix[t] / total:.0%})" for t in CATEGORY_TAGS))
    print(f"  Decisions    " + ", ".join(f"{k} {v}" for k, v in decisions.most_common()))
    print(f"  Intents      " + ", ".join(f"{k} {v}" for k, v in intents.most_common()))
    print(f"  Escalations  {sum(c.expected.must_escalate for c in cases)} (minimum {MIN_ESCALATIONS})")
    print(f"  Smoke        {sum('smoke' in c.tags for c in cases)} cases")

    if report.warnings:
        print("\nWarnings")
        for line in report.warnings:
            print(f"  {line}")
    if report.errors:
        print("\nErrors")
        for line in report.errors:
            print(f"  {line}")

    verdict = "FAIL" if report.errors else "PASS"
    print(f"\n{verdict}  {len(report.errors)} errors, {len(report.warnings)} warnings")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the Deflect golden dataset.")
    parser.add_argument("--golden", type=Path, default=GOLDEN_FILE)
    parser.add_argument("--policies", type=Path, default=POLICY_DIR)
    parser.add_argument("--offline", action="store_true", help="build the seed data in memory instead of reading Postgres")
    args = parser.parse_args()

    if args.offline:
        seed = load_seed_offline()
    else:
        import psycopg

        try:
            seed = load_seed_from_db()
        except psycopg.Error as exc:
            print("Could not read the seed database. Start it and seed it first:", file=sys.stderr)
            print("  docker compose up -d\n  python -m data.seed_orders", file=sys.stderr)
            print("Or run this check without a database: python -m evals.validate --offline", file=sys.stderr)
            print(f"\nDetails: {exc}", file=sys.stderr)
            return 2

    report, cases, policies = validate(args.golden, args.policies, seed)
    print_summary(report, cases, policies, seed)
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main())
