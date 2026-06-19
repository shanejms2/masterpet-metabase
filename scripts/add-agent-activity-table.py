#!/usr/bin/env python3
"""Add agent activity table (Voxbay calls + manual follow-ups) to dashboard 37."""

from __future__ import annotations

import json
import secrets
import subprocess
from datetime import datetime, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import agent_case_sql, agent_emails_sql, agent_phone_case_sql, agent_phones_sql

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_NAME = "Agent Activity — Calls & Follow-ups"

PERIOD_ID = "aff7e035-cf93-4608-9bc8-b996aed9a66e"

CARD_ROW = 103
CARD_SIZE_X = 24
CARD_SIZE_Y = 8

PERIOD_OPTIONS = [
    ["Today"],
    ["Yesterday"],
    ["Past 7 days"],
    ["Past 30 days"],
    ["This month"],
]

AGENT_PHONE_CASE = agent_phone_case_sql()
AGENT_EMAIL_CASE = agent_case_sql("agent", else_expr="ELSE 'Unknown'")
AGENT_PHONES_SQL = agent_phones_sql()
AGENT_EMAILS_SQL = agent_emails_sql()

SQL = f"""WITH CombinedActivity AS (
    SELECT
        {AGENT_PHONE_CASE} AS agent_name,
        start_time AS start_time,
        1 AS is_call,
        0 AS is_followup,
        CASE WHEN UPPER(status) IN ('ANSWER', 'ANSWERED', 'COMPLETED') THEN 1 ELSE 0 END AS answered,
        CASE WHEN UPPER(status) IN ('MISSED') THEN 1 ELSE 0 END AS missed,
        CASE WHEN UPPER(status) IN ('BUSY') THEN 1 ELSE 0 END AS busy,
        CASE WHEN UPPER(status) IN ('FAILED') THEN 1 ELSE 0 END AS failed,
        CASE WHEN UPPER(status) IN ('CANCEL', 'CANCELLED') THEN 1 ELSE 0 END AS cancelled,
        CASE WHEN UPPER(status) IN ('CHANUNAVAIL', 'UNAVAILABLE') THEN 1 ELSE 0 END AS unavailable
    FROM `tabVoxbay Call Log`
    WHERE agent_number IN ({AGENT_PHONES_SQL})

    UNION ALL

    SELECT
        {AGENT_EMAIL_CASE} AS agent_name,
        creation AS start_time,
        0 AS is_call,
        1 AS is_followup,
        0 AS answered,
        0 AS missed,
        0 AS busy,
        0 AS failed,
        0 AS cancelled,
        0 AS unavailable
    FROM `tabCustomer Follow Ups`
    WHERE agent IN ({AGENT_EMAILS_SQL})
)
SELECT
    agent_name AS `Agent`,
    SUM(is_followup) AS `Total Follow-ups`,
    SUM(answered) AS `Answered`,
    SUM(missed) AS `Missed`,
    SUM(busy) AS `Busy`,
    SUM(failed) AS `Failed`,
    SUM(cancelled) AS `Cancelled`,
    SUM(unavailable) AS `Unavailable`,
    SUM(is_call) AS `Total Voxbay Calls`
FROM CombinedActivity
WHERE DATE(start_time) >= CASE {{period}}
        WHEN 'Today' THEN CURDATE()
        WHEN 'Yesterday' THEN DATE_SUB(CURDATE(), INTERVAL 1 DAY)
        WHEN 'Past 7 days' THEN DATE_SUB(CURDATE(), INTERVAL 6 DAY)
        WHEN 'Past 30 days' THEN DATE_SUB(CURDATE(), INTERVAL 29 DAY)
        WHEN 'This month' THEN DATE_FORMAT(CURDATE(), '%Y-%m-01')
        ELSE CURDATE()
    END
  AND DATE(start_time) <= CASE {{period}}
        WHEN 'Today' THEN CURDATE()
        WHEN 'Yesterday' THEN DATE_SUB(CURDATE(), INTERVAL 1 DAY)
        ELSE CURDATE()
    END
GROUP BY agent_name
ORDER BY `Total Follow-ups` DESC, `Total Voxbay Calls` DESC;"""

TEMPLATE_TAGS = {
    "period": {
        "id": PERIOD_ID,
        "name": "period",
        "display-name": "Period",
        "type": "text",
        "default": "Today",
    },
}

CARD_PARAMETERS = [
    {
        "id": PERIOD_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "period"]],
        "name": "Period",
        "slug": "agent_period",
        "default": "Today",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": PERIOD_OPTIONS},
    },
]

DASHBOARD_PERIOD_PARAM = {
    "name": "Period",
    "slug": "agent_period",
    "id": PERIOD_ID,
    "type": "string/=",
    "sectionId": "string",
    "default": "Today",
    "values_query_type": "list",
    "values_source_type": "static-list",
    "values_source_config": {"values": PERIOD_OPTIONS},
}

VIZ = {
    "table.columns": [
        {"name": "Agent", "enabled": True},
        {"name": "Total Follow-ups", "enabled": True},
        {"name": "Total Voxbay Calls", "enabled": True},
        {"name": "Answered", "enabled": True},
        {"name": "Missed", "enabled": True},
        {"name": "Busy", "enabled": True},
        {"name": "Failed", "enabled": True},
        {"name": "Cancelled", "enabled": True},
        {"name": "Unavailable", "enabled": True},
    ],
    "column_settings": {
        '["name","Total Follow-ups"]': {"decimals": 0},
        '["name","Total Voxbay Calls"]': {"decimals": 0},
        '["name","Answered"]': {"decimals": 0},
        '["name","Missed"]': {"decimals": 0},
        '["name","Busy"]': {"decimals": 0},
        '["name","Failed"]': {"decimals": 0},
        '["name","Cancelled"]': {"decimals": 0},
        '["name","Unavailable"]': {"decimals": 0},
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


def upsert_card() -> int:
    existing = psql(
        f"SELECT id FROM report_card WHERE name = '{esc(CARD_NAME)}' AND archived = false LIMIT 1;"
    )
    dq = {
        "lib/type": "mbql/query",
        "database": 2,
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "native": SQL,
                "template-tags": TEMPLATE_TAGS,
            }
        ],
    }
    if existing:
        card_id = int(existing)
        psql(
            f"UPDATE report_card SET dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(CARD_PARAMETERS))}', "
            f"description = 'Defaults to Today. Use the Period dropdown on this card to change the range.', "
            f"visualization_settings = '{esc(json.dumps(VIZ))}', display = 'table', "
            f"cache_invalidated_at = NOW(), updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}")
        return card_id

    now = datetime.now(timezone.utc).isoformat()
    card_id = int(
        psql(
            f"""
INSERT INTO report_card (
    created_at, updated_at, name, description, display, dataset_query, visualization_settings,
    creator_id, database_id, query_type, collection_id, parameters, card_schema, type,
    entity_id, last_used_at
) VALUES (
    '{now}', '{now}', '{esc(CARD_NAME)}',
    'Defaults to Today. Use the Period dropdown on this card to change the range.',
    'table', '{esc(json.dumps(dq))}', '{esc(json.dumps(VIZ))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
)
RETURNING id;
"""
        )
    )
    print(f"Created card {card_id}")
    return card_id


def sync_dashboard_period_param() -> None:
    current_params = psql(f"SELECT parameters FROM report_dashboard WHERE id = {DASHBOARD_ID};")
    params = json.loads(current_params) if current_params else []
    params = [p for p in params if p.get("id") != PERIOD_ID]
    params.append(DASHBOARD_PERIOD_PARAM)
    psql(
        f"UPDATE report_dashboard SET parameters = '{esc(json.dumps(params))}', updated_at = NOW() "
        f"WHERE id = {DASHBOARD_ID};"
    )


def place_on_dashboard(card_id: int) -> None:
    inline_params = esc(json.dumps([PERIOD_ID]))
    mappings = esc(
        json.dumps(
            [
                {
                    "parameter_id": PERIOD_ID,
                    "card_id": card_id,
                    "target": ["variable", ["template-tag", "period"]],
                }
            ]
        )
    )
    sync_dashboard_period_param()

    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    if existing:
        psql(
            f"UPDATE report_dashboardcard SET row = {CARD_ROW}, col = 0, "
            f"size_x = {CARD_SIZE_X}, size_y = {CARD_SIZE_Y}, "
            f"parameter_mappings = '{mappings}', inline_parameters = '{inline_params}', updated_at = NOW() "
            f"WHERE id = {existing};"
        )
        return

    psql(
        f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id, inline_parameters
) VALUES (
    {CARD_SIZE_X}, {CARD_SIZE_Y}, {CARD_ROW}, 0, {card_id}, {DASHBOARD_ID},
    '{mappings}', '{{}}', '{entity_id()}', '{inline_params}'
);
"""
    )


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
    print(f"Done: dashboard {DASHBOARD_ID} — {CARD_NAME}")


if __name__ == "__main__":
    main()
