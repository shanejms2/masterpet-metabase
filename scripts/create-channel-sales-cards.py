#!/usr/bin/env python3
"""Create Metabase native SQL questions for Vennala store vs truck sales."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone

DATABASE_ID = 2
COLLECTION_ID = 16  # Daily Operations
CREATOR_ID = 1
DASHBOARD_ID = 36  # Company Dashboard
CARD_SCHEMA = 23

CHANNEL_TABLE_SQL = """SELECT
    si.posting_date AS date,
    CASE
        WHEN si.pos_profile = 'Vennala POS' THEN 'Vennala Store'
        ELSE 'Truck'
    END AS channel,
    ROUND(SUM(si.base_grand_total), 2) AS gross_sales,
    ROUND(SUM(si.base_net_total), 2) AS net_sales,
    ROUND(SUM(si.base_grand_total - si.base_net_total), 2) AS tax
FROM `tabSales Invoice` si
WHERE si.docstatus = 1
  [[AND si.posting_date >= {{start_date}}]]
  [[AND si.posting_date <= {{end_date}}]]
GROUP BY si.posting_date, channel
ORDER BY si.posting_date DESC, channel;"""

CHANNEL_LINE_SQL = """SELECT
    si.posting_date AS date,
    ROUND(SUM(CASE WHEN si.pos_profile = 'Vennala POS' THEN si.base_grand_total ELSE 0 END), 2) AS vennala_gross_sales,
    ROUND(SUM(CASE WHEN IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '' THEN si.base_grand_total ELSE 0 END), 2) AS truck_gross_sales,
    ROUND(SUM(si.base_grand_total), 2) AS total_gross_sales
FROM `tabSales Invoice` si
WHERE si.docstatus = 1
  [[AND si.posting_date >= {{start_date}}]]
  [[AND si.posting_date <= {{end_date}}]]
GROUP BY si.posting_date
ORDER BY si.posting_date ASC;"""

CHANNEL_STACKED_SQL = """SELECT
    si.posting_date AS date,
    CASE
        WHEN si.pos_profile = 'Vennala POS' THEN 'Vennala Store'
        ELSE 'Truck'
    END AS channel,
    ROUND(SUM(si.base_grand_total), 2) AS gross_sales
FROM `tabSales Invoice` si
WHERE si.docstatus = 1
  [[AND si.posting_date >= {{start_date}}]]
  [[AND si.posting_date <= {{end_date}}]]
GROUP BY si.posting_date, channel
ORDER BY si.posting_date ASC, channel;"""

DATE_PARAMETERS = [
    {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "type": "date/single",
        "target": ["variable", ["template-tag", "start_date"]],
        "name": "Start Date",
        "slug": "start_date",
        "default": None,
    },
    {
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "type": "date/single",
        "target": ["variable", ["template-tag", "end_date"]],
        "name": "End Date",
        "slug": "end_date",
        "default": None,
    },
]

TEMPLATE_TAGS = {
    "start_date": {
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "start_date",
        "display-name": "Start Date",
        "type": "date",
        "required": False,
        "default": None,
    },
    "end_date": {
        "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
        "name": "end_date",
        "display-name": "End Date",
        "type": "date",
        "required": False,
        "default": None,
    },
}


def entity_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(21))


def dataset_query(sql: str) -> str:
    payload = {
        "lib/type": "mbql/query",
        "database": DATABASE_ID,
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "native": sql,
                "template-tags": TEMPLATE_TAGS,
            }
        ],
    }
    return json.dumps(payload)


def psql(sql: str) -> str:
    cmd = [
        "docker",
        "exec",
        "metabase-postgres",
        "psql",
        "-U",
        "metabase",
        "-d",
        "metabase",
        "-t",
        "-A",
        "-c",
        sql,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def card_exists(name: str) -> int | None:
    safe_name = name.replace("'", "''")
    out = psql(
        f"SELECT id FROM report_card WHERE name = '{safe_name}' AND archived = false LIMIT 1;"
    )
    return int(out) if out else None


def insert_card(
    name: str,
    display: str,
    sql: str,
    visualization_settings: dict,
    parameters: list | None = None,
) -> int:
    existing = card_exists(name)
    if existing:
        print(f"SKIP exists: {name} (id={existing})")
        return existing

    now = datetime.now(timezone.utc).isoformat()
    eid = entity_id()
    params_json = json.dumps(parameters or DATE_PARAMETERS)
    viz_json = json.dumps(visualization_settings)
    dq = dataset_query(sql).replace("'", "''")
    safe_name = name.replace("'", "''")

    insert_sql = f"""
INSERT INTO report_card (
    created_at, updated_at, name, display, dataset_query, visualization_settings,
    creator_id, database_id, query_type, collection_id, parameters, card_schema, type,
    entity_id, last_used_at
) VALUES (
    '{now}', '{now}', '{safe_name}', '{display}', '{dq}', '{viz_json.replace("'", "''")}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID}, '{params_json.replace("'", "''")}',
    {CARD_SCHEMA}, 'question', '{eid}', '{now}'
)
RETURNING id;
"""
    card_id = int(psql(insert_sql))
    print(f"CREATED: {name} (id={card_id})")
    return card_id


def add_to_dashboard(card_id: int, row: int, col: int, size_x: int, size_y: int) -> None:
    exists = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    if exists:
        print(f"SKIP dashboard placement for card {card_id}")
        return

    eid = entity_id()
    insert_sql = f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id
) VALUES (
    {size_x}, {size_y}, {row}, {col}, {card_id}, {DASHBOARD_ID},
    '[]', '{{}}', '{eid}'
);
"""
    psql(insert_sql)
    print(f"ADDED card {card_id} to dashboard {DASHBOARD_ID} at row={row}, col={col}")


def main() -> int:
    cards = [
        {
            "name": "Daily Sales by Channel (Vennala vs Truck)",
            "display": "line",
            "sql": CHANNEL_LINE_SQL,
            "viz": {
                "graph.x_axis.scale": "timeseries",
                "graph.dimensions": ["date"],
                "graph.metrics": [
                    "vennala_gross_sales",
                    "truck_gross_sales",
                    "total_gross_sales",
                ],
            },
            "dashboard": {"row": 40, "col": 0, "size_x": 18, "size_y": 8},
        },
        {
            "name": "Daily Gross Sales by Channel (Stacked)",
            "display": "bar",
            "sql": CHANNEL_STACKED_SQL,
            "viz": {
                "graph.x_axis.scale": "timeseries",
                "graph.dimensions": ["date"],
                "graph.metrics": ["gross_sales"],
                "stackable.stack_type": "stacked",
                "graph.series_order_dimension": "channel",
            },
            "dashboard": {"row": 40, "col": 18, "size_x": 18, "size_y": 8},
        },
        {
            "name": "Daily Sales by Channel - Detailed Table",
            "display": "table",
            "sql": CHANNEL_TABLE_SQL,
            "viz": {
                "table.pivot_column": "channel",
                "table.cell_column": "gross_sales",
                "table.pivot": True,
            },
            "dashboard": {"row": 48, "col": 0, "size_x": 36, "size_y": 8},
        },
    ]

    created_ids: list[int] = []
    for spec in cards:
        card_id = insert_card(
            spec["name"],
            spec["display"],
            spec["sql"],
            spec["viz"],
        )
        created_ids.append(card_id)
        placement = spec["dashboard"]
        add_to_dashboard(
            card_id,
            placement["row"],
            placement["col"],
            placement["size_x"],
            placement["size_y"],
        )

    print("Done. Card IDs:", ", ".join(str(i) for i in created_ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
