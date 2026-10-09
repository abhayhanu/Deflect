import ast
from pathlib import Path
from typing import get_args

from agent.state import Intent
from evals.schema import INTENTS

AGENT_DIR = Path(__file__).parent
SERVER_DIR = AGENT_DIR.parent / "mcp_server"


def imported_packages(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def test_agent_never_imports_the_mcp_server():
    offenders = [p.relative_to(AGENT_DIR).as_posix() for p in AGENT_DIR.rglob("*.py") if "mcp_server" in imported_packages(p)]
    assert offenders == []


def test_mcp_server_depends_on_the_database_not_on_deflect():
    offenders = [
        p.relative_to(SERVER_DIR).as_posix()
        for p in SERVER_DIR.rglob("*.py")
        if "tests" not in p.parts and imported_packages(p) & {"agent", "data", "evals", "api"}
    ]
    assert offenders == []


def test_only_providers_names_a_provider_package():
    offenders = [
        p.name for p in AGENT_DIR.rglob("*.py")
        if p.name != "providers.py" and not p.name.startswith("test_")
        and any(pkg in p.read_text() for pkg in ("langchain_ollama", "langchain_openai", "langchain_anthropic"))
    ]
    assert offenders == []


def test_agent_intents_match_the_golden_dataset():
    assert get_args(Intent) == INTENTS


def test_the_plan_reasons_before_it_decides():
    """A hosted model writes the fields in the order the schema gives them. With the decision
    first it decided before it reasoned, and twice argued its way to the other answer too late."""
    from agent.nodes.plan import SYSTEM
    from agent.state import Plan

    fields = list(Plan.model_json_schema()["properties"])
    assert fields[:2] == ["rationale", "decision"]
    assert fields.index("tool_name") > fields.index("rationale")
    assert SYSTEM.index("- rationale:") < SYSTEM.index("- decision:")
