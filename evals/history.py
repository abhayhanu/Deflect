"""Reads the tables in EVALS.md back as data, for the console's metrics screen.

EVALS.md is where every recorded version lives, and its rows are only ever added. The results
files themselves are not in the repository. So the console reads the same file a person reads,
and the two can never show different numbers.
"""

import re
from pathlib import Path

from evals.gate import GATES
from evals.metrics import TARGETS
from evals.report import CATEGORY_START, EVALS_FILE, TABLE_START, fmt

COMPARISON_START = "## Model comparison"
NUMBER = re.compile(r"^(?:Rs )?(\d+(?:\.\d+)?)(?: s)?$")
GATED = {metric for metric, _, limit in GATES if limit is not None}
TARGET = re.compile(r"^([<>]?)\s*(?:Rs )?(\d+(?:\.\d+)?)")

# Column headings in the version table, and the metric each one holds.
VERSION_COLUMNS = {
    "Intent acc": "intent_accuracy", "Esc. recall": "escalation_recall", "Esc. precision": "escalation_precision",
    "Grounded": "groundedness", "Action correct": "action_correctness", "Forbidden tool rate": "forbidden_tool_rate",
    "Retrieval recall": "retrieval_recall", "Cost per ticket": "cost_inr_per_ticket", "Latency p50": "latency_s_p50",
    "Latency p95": "latency_s_p95",
}
# The version table has no deflection column. These come from the row for all tickets in the category table.
CATEGORY_COLUMNS = {"Decision acc": "decision_accuracy", "Deflection": "deflection_rate", "Reply quality": "reply_quality"}


def cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_after(text: str, heading: str) -> list[dict]:
    """The first table under a heading, one dict per row, keyed by the column headings."""
    if heading not in text:
        return []
    lines = text.split(heading, 1)[1].split("\n")
    start = next((i for i, line in enumerate(lines) if line.startswith("|")), None)
    if start is None:
        return []
    block = []
    for line in lines[start:]:
        if not line.startswith("|"):
            break
        block.append(cells(line))
    header, rows = block[0], block[2:]
    return [dict(zip(header, row)) for row in rows]


def number(text: str | None) -> float | None:
    found = NUMBER.match((text or "").strip())
    return float(found.group(1)) if found else None


def versions(text: str) -> list[dict]:
    overall = {row["Version"]: row for row in table_after(text, CATEGORY_START) if row.get("Category") == "all"}
    out = []
    for row in table_after(text, TABLE_START):
        metrics = {key: number(row.get(column)) for column, key in VERSION_COLUMNS.items()}
        extra = overall.get(row["Version"], {})
        metrics.update({key: number(extra.get(column)) for column, key in CATEGORY_COLUMNS.items()})
        out.append({"version": row["Version"], "change": row["Change"], "model": row["Model"], "cases": row["Cases"],
                    "date": row["Date"], "metrics": metrics})
    return out


def met(target: str, value: float | None) -> bool | None:
    """Whether a value meets a target as it is written in the build spec. None when the
    target is only reported, or when there is nothing to compare."""
    found = TARGET.match(target)
    if value is None or not found:
        return None
    sign, limit = found.group(1), float(found.group(2))
    if sign == ">":
        return value > limit
    return value < limit if sign == "<" else value <= limit


def in_ci(metric: str, gates: str) -> str:
    """What the gate really does with a metric today, which is not always what the target says.
    Action correctness is meant to be gated and its gate is still off."""
    if metric in GATED:
        return "hard gate, fails the build" if gates == "hard" else "gate, fails the build"
    if gates == "report":
        return "reported, never gated on purpose"
    return "reported, its gate is off for now" if gates in ("yes", "hard") else "reported"


def targets(latest: dict | None) -> list[dict]:
    """The ten headline numbers with their targets, and the last recorded version beside them."""
    values = dict((latest or {}).get("metrics") or {})
    rows = []
    for metric, (label, target, gates) in TARGETS.items():
        kind = "inr" if "inr" in metric else "ms" if "latency" in metric else "ratio"
        # The table keeps latency in seconds, and the target is written in seconds too.
        value = values.get("latency_s_p95") if kind == "ms" else values.get(metric)
        shown = fmt(None if value is None else value * 1000, "ms") if kind == "ms" else fmt(value, kind)
        rows.append({"metric": metric, "label": label, "target": target, "gates": gates, "value": value,
                     "shown": shown, "met": met(target, value), "ci": in_ci(metric, gates)})
    return rows


def history(path: Path = EVALS_FILE) -> dict:
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    recorded = versions(text)
    return {"versions": recorded, "targets": targets(recorded[-1] if recorded else None),
            "latest": recorded[-1]["version"] if recorded else None, "comparison": table_after(text, COMPARISON_START)}
