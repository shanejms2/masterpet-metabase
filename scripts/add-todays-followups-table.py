#!/usr/bin/env python3
"""Today's follow-up log table for dashboard 37 (card 189)."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from datetime import datetime, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import agent_case_sql, agent_emails_sql
from pathlib import Path

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_ID = 189
CARD_NAME = "Today's Follow-ups"
FOLLOWUP_DAY_ID = "f189a001-0001-4000-8000-000000000189"
CARD_DESCRIPTION = "Follow-ups for the selected day (defaults to today), newest first."

AGENT_EMAILS_SQL = agent_emails_sql()
AGENT_NAME_CASE = agent_case_sql("cfu.agent", else_expr="ELSE cfu.agent")

SQL = f"""
SELECT
    DATE_FORMAT(cfu.creation, '%H:%i') AS `Time`,
    {AGENT_NAME_CASE} AS `Agent`,
    COALESCE(NULLIF(TRIM(cfu.customer_name), ''), c.customer_name, cfu.customer) AS `Customer`,
    cfu.customer AS `Customer ID`,
    cfu.call_outcome AS `Outcome`,
    NULLIF(TRIM(cfu.quick_note), '') AS `Notes`,
    cfu.next_follow_up AS `Next Follow-up`
FROM `tabCustomer Follow Ups` cfu
LEFT JOIN `tabCustomer` c ON c.name = cfu.customer
WHERE DATE(cfu.creation) = [[{{{{followup_day}}}} --]] CURDATE()
  AND cfu.agent IN ({AGENT_EMAILS_SQL})
ORDER BY cfu.creation DESC;"""

TEMPLATE_TAGS = {
    "followup_day": {
        "id": FOLLOWUP_DAY_ID,
        "name": "followup_day",
        "display-name": "Day",
        "type": "date",
        "required": False,
    },
}

CARD_PARAMETERS = [
    {
        "id": FOLLOWUP_DAY_ID,
        "type": "date/single",
        "target": ["variable", ["template-tag", "followup_day"]],
        "name": "Follow-up Day",
        "slug": "followup_day",
        "default": None,
    },
]

DASHBOARD_FOLLOWUP_DAY_PARAM = {
    "name": "Follow-up Day",
    "slug": "followup_day",
    "id": FOLLOWUP_DAY_ID,
    "type": "date/single",
    "sectionId": "date",
    "default": None,
}

VIZ = {
    "table.pivot": False,
    "table.cell_column": "Time",
    "table.columns": [
        {"name": "Time", "enabled": True},
        {"name": "Agent", "enabled": True},
        {"name": "Customer", "enabled": True},
        {"name": "Outcome", "enabled": True},
        {"name": "Notes", "enabled": True},
        {"name": "Next Follow-up", "enabled": True},
        {"name": "Customer ID", "enabled": False},
    ],
    "table.column_formatting": [
        {
            "id": 0,
            "type": "single",
            "operator": "=",
            "value": "Connected",
            "columns": ["Outcome"],
            "color": "#2D4A35",
            "highlight_row": False,
        },
        {
            "id": 1,
            "type": "single",
            "operator": "=",
            "value": "No Answer",
            "columns": ["Outcome"],
            "color": "#5C3030",
            "highlight_row": False,
        },
        {
            "id": 2,
            "type": "single",
            "operator": "=",
            "value": "Busy",
            "columns": ["Outcome"],
            "color": "#5A4A3D",
            "highlight_row": False,
        },
    ],
    "column_settings": {
        '["name","Time"]': {"column_title": "Time", "text_align": "left"},
        '["name","Agent"]': {"text_align": "left"},
        '["name","Customer"]': {
            "column_title": "Customer",
            "click_behavior": {
                "type": "link",
                "linkType": "url",
                "linkTemplate": "https://dashboard.masterpet.co.in/crm/customer/{{Customer ID}}",
            },
        },
        '["name","Outcome"]': {"text_align": "left"},
        '["name","Notes"]': {"text_align": "left"},
        '["name","Next Follow-up"]': {
            "date_style": "MMM D, YYYY",
            "text_align": "left",
        },
    },
}


def entity_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(21))


def esc(s: str) -> str:
    return s.replace("'", "''")


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "metabase-postgres", "psql", "-U", "metabase", "-d", "metabase", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def followup_day_mappings(card_id: int = CARD_ID) -> list[dict]:
    return [
        {
            "parameter_id": FOLLOWUP_DAY_ID,
            "card_id": card_id,
            "target": ["variable", ["template-tag", "followup_day"]],
        }
    ]


def sync_dashboard_followup_day_param() -> None:
    current = psql(f"SELECT parameters FROM report_dashboard WHERE id = {DASHBOARD_ID};")
    params = json.loads(current) if current else []
    params = [p for p in params if p.get("id") != FOLLOWUP_DAY_ID]
    params.append(DASHBOARD_FOLLOWUP_DAY_PARAM)
    psql(
        f"UPDATE report_dashboard SET parameters = '{esc(json.dumps(params))}', updated_at = NOW() "
        f"WHERE id = {DASHBOARD_ID};"
    )


def upsert_card() -> None:
    dq = {
        "lib/type": "mbql/query",
        "database": DATABASE_ID,
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "native": SQL,
                "template-tags": TEMPLATE_TAGS,
            }
        ],
    }
    existing = psql(f"SELECT id FROM report_card WHERE id = {CARD_ID} LIMIT 1;")
    if existing:
        psql(
            f"UPDATE report_card SET name = '{esc(CARD_NAME)}', "
            f"description = '{esc(CARD_DESCRIPTION)}', "
            f"display = 'table', dataset_query = '{esc(json.dumps(dq))}', "
            f"visualization_settings = '{esc(json.dumps(VIZ))}', "
            f"parameters = '{esc(json.dumps(CARD_PARAMETERS))}', "
            f"cache_ttl = 300, cache_invalidated_at = NOW(), updated_at = NOW() "
            f"WHERE id = {CARD_ID};"
        )
        print(f"Updated card {CARD_ID}: {CARD_NAME}")
        return

    now = datetime.now(timezone.utc).isoformat()
    psql(
        f"""
INSERT INTO report_card (
    id, created_at, updated_at, name, description, display, dataset_query,
    visualization_settings, creator_id, database_id, query_type, collection_id,
    parameters, card_schema, type, entity_id, last_used_at, cache_ttl
) VALUES (
    {CARD_ID}, '{now}', '{now}', '{esc(CARD_NAME)}',
    '{esc(CARD_DESCRIPTION)}',
    'table', '{esc(json.dumps(dq))}', '{esc(json.dumps(VIZ))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question', '{entity_id()}', '{now}', 300
);
"""
    )
    print(f"Created card {CARD_ID}: {CARD_NAME}")


def main() -> None:
    upsert_card()

    _spec = importlib.util.spec_from_file_location(
        "reorganize_dashboard_37",
        Path(__file__).with_name("reorganize-dashboard-37.py"),
    )
    mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(mod)
    mod.main()


if __name__ == "__main__":
    main()
