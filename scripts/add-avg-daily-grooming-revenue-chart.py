#!/usr/bin/env python3
"""Grooming revenue charts by channel for dashboard 37 (cards 196–197)."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23

INVOICE_LINES_CTE = """InvoiceLines AS (
    SELECT
        DATE_FORMAT(si.posting_date, '%Y-%m-01') AS month_date,
        si.name AS invoice_id,
        CASE
            WHEN si.pos_profile = 'Vennala POS' THEN 'Store'
            ELSE 'Truck'
        END AS channel,
        CASE
            WHEN sii.item_group IN ('Grooming Services', 'Travel Services') THEN 'Grooming & Travel'
            ELSE 'Product'
        END AS category,
        sii.item_group,
        sii.item_code,
        sii.qty,
        sii.base_amount
            + (si.base_grand_total - SUM(sii.base_amount) OVER (PARTITION BY si.name))
              * (sii.base_amount / NULLIF(SUM(sii.base_amount) OVER (PARTITION BY si.name), 0)) AS gross_with_tax
    FROM `tabSales Invoice` si
    INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND si.posting_date < CURDATE()
)"""

GROOMING_SMART_QTY = """CASE
            WHEN SUM(
                CASE
                    WHEN item_group = 'Grooming Services' AND item_code NOT LIKE '%ADD%'
                    THEN qty ELSE 0
                END
            ) > 0
            THEN SUM(
                CASE
                    WHEN item_group = 'Grooming Services' AND item_code NOT LIKE '%ADD%'
                    THEN qty ELSE 0
                END
            )
            ELSE MAX(
                CASE
                    WHEN item_group = 'Grooming Services' AND item_code LIKE '%ADD%'
                    THEN qty ELSE 0
                END
            )
        END"""

AVG_DAILY_GROOMING_REVENUE_SQL = f"""WITH {INVOICE_LINES_CTE},
MonthlyGroomingRevenue AS (
    SELECT
        month_date,
        channel,
        SUM(gross_with_tax) AS grooming_revenue
    FROM InvoiceLines
    WHERE category = 'Grooming & Travel'
    GROUP BY month_date, channel
)
SELECT
    mg.month_date AS month,
    mg.channel,
    ROUND(mg.grooming_revenue, 0) AS `Grooming Revenue`,
    DATEDIFF(
        LEAST(LAST_DAY(mg.month_date), CURDATE() - INTERVAL 1 DAY),
        mg.month_date
    ) + 1 AS `Days Counted`,
    ROUND(
        mg.grooming_revenue / (
            DATEDIFF(
                LEAST(LAST_DAY(mg.month_date), CURDATE() - INTERVAL 1 DAY),
                mg.month_date
            ) + 1
        ),
        0
    ) AS `Avg Daily Grooming Revenue`
FROM MonthlyGroomingRevenue mg
WHERE mg.grooming_revenue > 0
ORDER BY mg.month_date ASC, mg.channel;"""

AVG_REVENUE_PER_GROOMING_SQL = f"""WITH {INVOICE_LINES_CTE},
MonthlyGroomingRevenue AS (
    SELECT
        month_date,
        channel,
        SUM(gross_with_tax) AS grooming_revenue
    FROM InvoiceLines
    WHERE category = 'Grooming & Travel'
    GROUP BY month_date, channel
),
InvoiceGroomings AS (
    SELECT
        month_date,
        channel,
        invoice_id,
        {GROOMING_SMART_QTY} AS smart_qty
    FROM InvoiceLines
    WHERE category = 'Grooming & Travel'
    GROUP BY month_date, channel, invoice_id
),
MonthlyGroomings AS (
    SELECT
        month_date,
        channel,
        SUM(smart_qty) AS groomings
    FROM InvoiceGroomings
    WHERE smart_qty > 0
    GROUP BY month_date, channel
)
SELECT
    mr.month_date AS month,
    mr.channel,
    ROUND(mr.grooming_revenue, 0) AS `Grooming Revenue`,
    mg.groomings AS `Groomings`,
    DATEDIFF(
        LEAST(LAST_DAY(mr.month_date), CURDATE() - INTERVAL 1 DAY),
        mr.month_date
    ) + 1 AS `Days Counted`,
    ROUND(
        (mr.grooming_revenue / (
            DATEDIFF(
                LEAST(LAST_DAY(mr.month_date), CURDATE() - INTERVAL 1 DAY),
                mr.month_date
            ) + 1
        )) / NULLIF(
            mg.groomings / (
                DATEDIFF(
                    LEAST(LAST_DAY(mr.month_date), CURDATE() - INTERVAL 1 DAY),
                    mr.month_date
                ) + 1
            ),
            0
        ),
        0
    ) AS `Avg Daily Revenue per Grooming`
FROM MonthlyGroomingRevenue mr
INNER JOIN MonthlyGroomings mg
    ON mg.month_date = mr.month_date
   AND mg.channel = mr.channel
WHERE mr.grooming_revenue > 0
ORDER BY mr.month_date ASC, mr.channel;"""

CHANNEL_SERIES = {
    "Store": {"color": "#509EE3", "title": "Vennala Store", "display": "line"},
    "Truck": {"color": "#88BF4D", "title": "Truck", "display": "line"},
}

CARDS = [
    {
        "id": 196,
        "name": "Avg Daily Grooming Revenue by Channel",
        "description": (
            "Average daily grooming & travel revenue by channel. Grooming revenue uses "
            "line-item tax allocation across the full invoice (matches Monthly Sales by "
            "Channel). Current month excludes today."
        ),
        "sql": AVG_DAILY_GROOMING_REVENUE_SQL,
        "viz": {
            "graph.dimensions": ["month", "channel"],
            "graph.metrics": ["Avg Daily Grooming Revenue"],
            "graph.x_axis.scale": "timeseries",
            "graph.x_axis.title_text": "Month",
            "graph.y_axis.title_text": "Avg daily grooming revenue (incl. tax)",
            "graph.y_axis.auto_split": False,
            "graph.show_values": False,
            "series_settings": CHANNEL_SERIES,
            "column_settings": {
                '["name","Grooming Revenue"]': {"prefix": "₹", "number_separators": ","},
                '["name","Avg Daily Grooming Revenue"]': {"prefix": "₹", "number_separators": ","},
            },
        },
    },
    {
        "id": 197,
        "name": "Avg Daily Revenue per Grooming by Channel",
        "description": (
            "Average daily grooming revenue divided by average daily groomings per channel "
            "(equals grooming revenue per grooming for the month). Matches MTD Groomings & "
            "Daily Averages. Current month excludes today."
        ),
        "sql": AVG_REVENUE_PER_GROOMING_SQL,
        "viz": {
            "graph.dimensions": ["month", "channel"],
            "graph.metrics": ["Avg Daily Revenue per Grooming"],
            "graph.x_axis.scale": "timeseries",
            "graph.x_axis.title_text": "Month",
            "graph.y_axis.title_text": "Avg daily revenue per grooming (incl. tax)",
            "graph.y_axis.auto_split": False,
            "graph.show_values": False,
            "series_settings": CHANNEL_SERIES,
            "column_settings": {
                '["name","Grooming Revenue"]': {"prefix": "₹", "number_separators": ","},
                '["name","Avg Daily Revenue per Grooming"]': {"prefix": "₹", "number_separators": ","},
            },
        },
    },
]


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


def upsert_card(spec: dict) -> None:
    card_id = spec["id"]
    dq = {
        "lib/type": "mbql/query",
        "database": DATABASE_ID,
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "native": spec["sql"],
                "template-tags": {},
            }
        ],
    }
    existing = psql(f"SELECT id FROM report_card WHERE id = {card_id} LIMIT 1;")
    if existing:
        psql(
            f"UPDATE report_card SET name = '{esc(spec['name'])}', "
            f"description = '{esc(spec['description'])}', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '[]', "
            f"visualization_settings = '{esc(json.dumps(spec['viz']))}', "
            f"display = 'line', cache_invalidated_at = NOW(), updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}: {spec['name']}")
        return

    now = datetime.now(timezone.utc).isoformat()
    psql(
        f"""
INSERT INTO report_card (
    id, created_at, updated_at, name, description, display, dataset_query,
    visualization_settings, creator_id, database_id, query_type, collection_id,
    parameters, card_schema, type, entity_id, last_used_at
) VALUES (
    {card_id}, '{now}', '{now}', '{esc(spec['name'])}', '{esc(spec['description'])}',
    'line', '{esc(json.dumps(dq))}', '{esc(json.dumps(spec['viz']))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '[]', {CARD_SCHEMA}, 'question', '{entity_id()}', '{now}'
);
"""
    )
    print(f"Created card {card_id}: {spec['name']}")


def main() -> None:
    for spec in CARDS:
        upsert_card(spec)

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
