#!/usr/bin/env python3
"""Past 7 days daily sales with channel & category breakdown (cards 187–188)."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "top",
    Path(__file__).with_name("add-dashboard-top-kpis.py"),
)
_top = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_top)

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
CARD_SCHEMA = 23
CHART_CARD_ID = 187
TABLE_CARD_ID = 188

WEEK_START = "DATE_SUB(CURDATE(), INTERVAL 7 DAY)"
WEEK_END_EXCLUSIVE = "CURDATE()"

ALLOCATED_LINE = """sii.base_amount
            + (si.base_grand_total - SUM(sii.base_amount) OVER (PARTITION BY si.name))
              * (sii.base_amount / NULLIF(SUM(sii.base_amount) OVER (PARTITION BY si.name), 0))"""

CHANNEL_CASE = """CASE
            WHEN si.pos_profile = 'Vennala POS' THEN 'Vennala Store'
            ELSE 'Truck'
        END"""

CATEGORY_CASE = """CASE
            WHEN sii.item_group IN ('Grooming Services', 'Travel Services') THEN 'Grooming & Travel'
            ELSE 'Product'
        END"""

INVOICE_LINES_CTE = f"""
InvoiceLines AS (
    SELECT
        si.posting_date AS day_date,
        {CHANNEL_CASE} AS channel,
        {CATEGORY_CASE} AS sales_type,
        {ALLOCATED_LINE} AS gross_with_tax
    FROM `tabSales Invoice` si
    INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND si.posting_date >= {WEEK_START}
      AND si.posting_date < {WEEK_END_EXCLUSIVE}
)"""

CHART_SQL = f"""
WITH RECURSIVE DateRange AS (
    SELECT {WEEK_START} AS day_date
    UNION ALL
    SELECT DATE_ADD(day_date, INTERVAL 1 DAY)
    FROM DateRange
    WHERE day_date < DATE_SUB(CURDATE(), INTERVAL 1 DAY)
),
Segments AS (
    SELECT 'Vennala Store' AS channel, 'Grooming & Travel' AS sales_type
    UNION ALL SELECT 'Vennala Store', 'Product'
    UNION ALL SELECT 'Truck', 'Grooming & Travel'
    UNION ALL SELECT 'Truck', 'Product'
),
{INVOICE_LINES_CTE},
DailySales AS (
    SELECT
        day_date,
        channel,
        sales_type,
        SUM(gross_with_tax) AS gross_inr
    FROM InvoiceLines
    GROUP BY day_date, channel, sales_type
)
SELECT
    dr.day_date AS date,
    CONCAT(seg.channel, ' · ', seg.sales_type) AS segment,
    ROUND(COALESCE(ds.gross_inr, 0), 0) AS gross_sales_inr
FROM DateRange dr
CROSS JOIN Segments seg
LEFT JOIN DailySales ds
    ON ds.day_date = dr.day_date
   AND ds.channel = seg.channel
   AND ds.sales_type = seg.sales_type
ORDER BY dr.day_date ASC, seg.channel, seg.sales_type;"""

TABLE_SQL = f"""
WITH RECURSIVE DateRange AS (
    SELECT {WEEK_START} AS day_date
    UNION ALL
    SELECT DATE_ADD(day_date, INTERVAL 1 DAY)
    FROM DateRange
    WHERE day_date < DATE_SUB(CURDATE(), INTERVAL 1 DAY)
),
{INVOICE_LINES_CTE},
DailyBySegment AS (
    SELECT
        day_date,
        channel,
        sales_type,
        ROUND(SUM(gross_with_tax), 0) AS amount
    FROM InvoiceLines
    GROUP BY day_date, channel, sales_type
),
DailyPivot AS (
    SELECT
        dr.day_date,
        COALESCE(MAX(CASE
            WHEN d.channel = 'Vennala Store' AND d.sales_type = 'Grooming & Travel' THEN d.amount
        END), 0) AS store_grooming,
        COALESCE(MAX(CASE
            WHEN d.channel = 'Vennala Store' AND d.sales_type = 'Product' THEN d.amount
        END), 0) AS store_product,
        COALESCE(MAX(CASE
            WHEN d.channel = 'Truck' AND d.sales_type = 'Grooming & Travel' THEN d.amount
        END), 0) AS truck_grooming,
        COALESCE(MAX(CASE
            WHEN d.channel = 'Truck' AND d.sales_type = 'Product' THEN d.amount
        END), 0) AS truck_product
    FROM DateRange dr
    LEFT JOIN DailyBySegment d ON d.day_date = dr.day_date
    GROUP BY dr.day_date
)
SELECT
    DATE_FORMAT(day_date, '%d %b') AS `Date`,
    store_grooming + store_product + truck_grooming + truck_product AS `Total`,
    store_grooming AS `Store · Grooming`,
    store_product AS `Store · Product`,
    truck_grooming AS `Truck · Grooming`,
    truck_product AS `Truck · Product`
FROM DailyPivot
ORDER BY day_date ASC;"""

CHART_VIZ = {
    "stackable.stack_type": "stacked",
    "graph.x_axis.scale": "timeseries",
    "graph.dimensions": ["date", "segment"],
    "graph.metrics": ["gross_sales_inr"],
    "graph.show_values": True,
    "graph.label_value_formatting": "full",
    "graph.y_axis.title_text": "Gross Sales (₹ incl. tax)",
    "graph.x_axis.title_text": "",
    "graph.show_legend": True,
    "column_settings": {
        '["name","gross_sales_inr"]': {
            "number_style": "currency",
            "currency": "INR",
            "currency_style": "symbol",
            "decimals": 0,
        },
    },
    "series_settings": {
        "Vennala Store · Grooming & Travel": {"color": "#509EE3", "title": "Store · Grooming"},
        "Vennala Store · Product": {"color": "#A989C5", "title": "Store · Product"},
        "Truck · Grooming & Travel": {"color": "#88BF4D", "title": "Truck · Grooming"},
        "Truck · Product": {"color": "#F9D45C", "title": "Truck · Product"},
    },
}

INR_COLUMN = {
    "text_align": "right",
    "number_style": "currency",
    "currency": "INR",
    "currency_style": "symbol",
    "decimals": 0,
}

TABLE_VIZ = {
    "table.pivot": False,
    "table.cell_column": "Date",
    "table.columns": [
        {"name": "Date", "enabled": True},
        {"name": "Total", "enabled": True},
        {"name": "Store · Grooming", "enabled": True},
        {"name": "Store · Product", "enabled": True},
        {"name": "Truck · Grooming", "enabled": True},
        {"name": "Truck · Product", "enabled": True},
    ],
    "table.column_formatting": [
        {
            "id": 0,
            "type": "single",
            "operator": "=",
            "value": 0,
            "columns": ["Store · Grooming"],
            "color": "#5C3030",
            "highlight_row": False,
        },
        {
            "id": 1,
            "type": "single",
            "operator": "=",
            "value": 0,
            "columns": ["Truck · Grooming"],
            "color": "#5C3030",
            "highlight_row": False,
        },
        {
            "id": 2,
            "type": "single",
            "operator": ">",
            "value": 20000,
            "columns": ["Total"],
            "color": "#2D4A35",
            "highlight_row": False,
        },
    ],
    "column_settings": {
        '["name","Date"]': {"column_title": "Date", "text_align": "left"},
        '["name","Store · Grooming"]': INR_COLUMN,
        '["name","Store · Product"]': INR_COLUMN,
        '["name","Truck · Grooming"]': INR_COLUMN,
        '["name","Truck · Product"]': INR_COLUMN,
        '["name","Total"]': {**INR_COLUMN, "column_title": "Total"},
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


def upsert_card(card_id: int, name: str, description: str, display: str, sql: str, viz: dict) -> None:
    dq = {
        "lib/type": "mbql/query",
        "database": DATABASE_ID,
        "stages": [{"lib/type": "mbql.stage/native", "native": sql, "template-tags": {}}],
    }
    existing = psql(f"SELECT id FROM report_card WHERE id = {card_id} LIMIT 1;")
    if existing:
        psql(
            f"UPDATE report_card SET name = '{esc(name)}', description = '{esc(description)}', "
            f"display = '{esc(display)}', dataset_query = '{esc(json.dumps(dq))}', "
            f"visualization_settings = '{esc(json.dumps(viz))}', updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}: {name}")
        return

    now = datetime.now(timezone.utc).isoformat()
    psql(
        f"""
INSERT INTO report_card (
    id, created_at, updated_at, name, description, display, dataset_query,
    visualization_settings, creator_id, database_id, query_type, collection_id,
    parameters, card_schema, type, entity_id, last_used_at
) VALUES (
    {card_id}, '{now}', '{now}', '{esc(name)}', '{esc(description)}', '{esc(display)}',
    '{esc(json.dumps(dq))}', '{esc(json.dumps(viz))}', {CREATOR_ID}, {DATABASE_ID},
    'native', {COLLECTION_ID}, '[]', {CARD_SCHEMA}, 'question', '{entity_id()}', '{now}'
);
"""
    )
    print(f"Created card {card_id}: {name}")


def main() -> None:
    upsert_card(
        CHART_CARD_ID,
        "Past 7 Days — Daily Sales",
        "Stacked daily gross sales by store/truck and grooming/product (incl. tax, excl. today)",
        "bar",
        CHART_SQL,
        CHART_VIZ,
    )
    upsert_card(
        TABLE_CARD_ID,
        "Past 7 Days — Daily Sales Detail",
        "Daily breakdown in ₹ by channel and category",
        "table",
        TABLE_SQL,
        TABLE_VIZ,
    )

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
