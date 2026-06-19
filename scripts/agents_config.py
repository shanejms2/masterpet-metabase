"""Load agent identity mapping from data/agents.json (gitignored on prod)."""

from __future__ import annotations

import json
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_AGENTS_FILE = _DATA_DIR / "agents.json"
_EXAMPLE_FILE = _DATA_DIR / "agents.example.json"


def load_agents() -> list[dict[str, str]]:
    path = _AGENTS_FILE if _AGENTS_FILE.exists() else _EXAMPLE_FILE
    if not path.exists():
        raise FileNotFoundError(
            f"Agent config missing. Copy {_EXAMPLE_FILE} to {_AGENTS_FILE} and fill in real values."
        )
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    agents = data.get("agents", [])
    if not agents:
        raise ValueError(f"No agents defined in {path}")
    return agents


def agent_tuples() -> list[tuple[str, str]]:
    """Return (name, email) pairs in config order."""
    return [(a["name"], a["email"]) for a in load_agents()]


def agent_emails_sql() -> str:
    return ", ".join(f"'{a['email']}'" for a in load_agents())


def agent_names_field_order() -> str:
    return ", ".join(f"'{a['name']}'" for a in load_agents())


def agent_case_sql(column: str, *, prefix: str = "", else_expr: str | None = None) -> str:
    col = f"{prefix}{column}" if prefix else column
    lines = [f"WHEN {col} = '{a['email']}' THEN '{a['name']}'" for a in load_agents()]
    tail = else_expr if else_expr is not None else f"ELSE {col}"
    return "CASE\n            " + "\n            ".join(lines) + f"\n            {tail}\n        END"


def agents_union_sql() -> str:
    parts: list[str] = []
    for i, agent in enumerate(load_agents()):
        if i == 0:
            parts.append(
                f"SELECT '{agent['name']}' AS agent_name, '{agent['email']}' AS email"
            )
        else:
            parts.append(f"UNION ALL SELECT '{agent['name']}', '{agent['email']}'")
    return "\n    ".join(parts)


def agent_logged_cols_sql() -> str:
    return ",\n        ".join(
        f"SUM(CASE WHEN cfu.agent = '{a['email']}' THEN 1 ELSE 0 END) AS `{a['name']}`"
        for a in load_agents()
    )


def agent_phone_case_sql(column: str = "agent_number", *, else_expr: str = "ELSE 'Unknown'") -> str:
    lines = [
        f"WHEN {column} = '{a['phone']}' THEN '{a['name']}'"
        for a in load_agents()
        if a.get("phone")
    ]
    return "CASE\n            " + "\n            ".join(lines) + f"\n            {else_expr}\n        END"


def agent_phones_sql() -> str:
    phones = [a["phone"] for a in load_agents() if a.get("phone")]
    return ", ".join(f"'{p}'" for p in phones)
