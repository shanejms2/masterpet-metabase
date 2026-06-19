#!/usr/bin/env python3
"""Add daily sales by channel question to Metabase dashboard 37 (New)."""

from __future__ import annotations

import json
import secrets
import subprocess
import sys
import uuid
from datetime import datetime, timezone

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_NAME = "Daily Sales by Channel"

DATE_FILTER_TAG_ID = str(uuid.uuid4())
DASHBOARD_DATE_PARAM_ID = str(uuid.uuid4())

SQL = """WITH RECURSIVE DateRange AS (
    SELECT DATE_SUB(CURDATE(), INTERVAL 400 DAY) AS date_point
    UNION ALL
    SELECT DATE_ADD(date_point, INTERVAL 1 DAY)
    FROM DateRange
    WHERE date_point < CURDATE()
),
Channels AS (
    SELECT 'Vennala Store' AS channel
    UNION ALL
    SELECT 'Truck' AS channel
),
DailySales AS (
    SELECT
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
    GROUP BY si.posting_date, channel
)
SELECT
    dr.date_point AS date,
    c.channel,
    COALESCE(ds.gross_sales, 0) AS gross_sales,
    COALESCE(ds.net_sales, 0) AS net_sales,
    COALESCE(ds.tax, 0) AS tax
FROM DateRange dr
CROSS JOIN Channels c
LEFT JOIN DailySales ds
    ON ds.date = dr.date_point
   AND ds.channel = c.channel
WHERE {{date_filter}}
ORDER BY dr.date_point DESC, c.channel;"""

TEMPLATE_TAGS = {
    "date_filter": {
        "id": DATE_FILTER_TAG_ID,
        "name": "date_filter",
        "display-name": "Date Filter",
        "type": "dimension",
        "widget-type": "date/range",
        "dimension": ["field", {"lib/uuid": str(uuid.uuid4())}, 15000],
        "alias": "dr.date_point",
        "default": "past3months~",
    }
}

CARD_PARAMETERS = [
    {
        "id": DATE_FILTER_TAG_ID,
        "type": "date/range",
        "target": ["dimension", ["template-tag", "date_filter"]],
        "name": "Date Filter",
        "slug": "date_filter",
        "default": "past3months~",
        "isMultiSelect": True,
    }
]

DASHBOARD_PARAMETERS = [
    {
        "name": "Date",
        "slug": "date",
        "id": DASHBOARD_DATE_PARAM_ID,
        "type": "date/range",
        "sectionId": "date",
        "default": "past3months~",
    }
]

VISUALIZATION_SETTINGS = {
    "stackable.stack_type": "stacked",
    "graph.x_axis.scale": "timeseries",
    "graph.dimensions": ["date", "channel"],
    "graph.metrics": ["gross_sales"],
    "graph.show_values": False,
    "series_settings": {
        "Vennala Store": {"color": "#509EE3", "title": "Vennala Store"},
        "Truck": {"color": "#88BF4D", "title": "Truck"},
    },
}


def entity_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(21))


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


def esc(value: str) -> str:
    return value.replace("'", "''")


def main() -> int:
    existing = psql(
        f"SELECT id FROM report_card WHERE name = '{esc(CARD_NAME)}' AND archived = false LIMIT 1;"
    )
    if existing:
        card_id = int(existing)
        print(f"Using existing card id={card_id}")
    else:
        now = datetime.now(timezone.utc).isoformat()
        dataset_query = json.dumps(
            {
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
        )
        card_id = int(
            psql(
                f"""
INSERT INTO report_card (
    created_at, updated_at, name, display, dataset_query, visualization_settings,
    creator_id, database_id, query_type, collection_id, parameters, card_schema, type,
    entity_id, last_used_at
) VALUES (
    '{now}', '{now}', '{esc(CARD_NAME)}', 'bar', '{esc(dataset_query)}',
    '{esc(json.dumps(VISUALIZATION_SETTINGS))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
)
RETURNING id;
"""
            )
        )
        print(f"Created card id={card_id}")

    dashboard_params = esc(json.dumps(DASHBOARD_PARAMETERS))
    psql(
        f"UPDATE report_dashboard SET parameters = '{dashboard_params}', updated_at = NOW() WHERE id = {DASHBOARD_ID};"
    )
    print("Updated dashboard 37 with date range filter")

    placement = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    parameter_mappings = json.dumps(
        [
            {
                "parameter_id": DASHBOARD_DATE_PARAM_ID,
                "card_id": card_id,
                "target": ["dimension", ["template-tag", "date_filter"], {"stage-number": 0}],
            }
        ]
    )

    if placement:
        psql(
            f"""
UPDATE report_dashboardcard
SET parameter_mappings = '{esc(parameter_mappings)}', updated_at = NOW()
WHERE id = {placement};
"""
        )
        print(f"Updated dashboard card placement id={placement}")
    else:
        psql(
            f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id
) VALUES (
    24, 10, 0, 0, {card_id}, {DASHBOARD_ID},
    '{esc(parameter_mappings)}', '{{}}', '{entity_id()}'
);
"""
        )
        print("Added card to dashboard 37")

    print(f"Done: https://metabase.masterpet.co.in/dashboard/37-new")
    return 0


if __name__ == "__main__":
    sys.exit(main())
