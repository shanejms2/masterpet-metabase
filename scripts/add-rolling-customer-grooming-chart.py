#!/usr/bin/env python3
"""Add combined 30-day rolling customers & groomings chart to dashboard 37."""

from __future__ import annotations

import json
import secrets
import subprocess
import uuid
from datetime import datetime, timezone

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_NAME = "30-Day Rolling Customers & Groomings"

DATE_FILTER_ID = "1d83f393-9646-478c-9532-c2a457d646ed"
CHANNEL_ID = "c8d7e6f5-a4b3-4c2d-9e1f-0a9b8c7d6e5f"

CHART_ROW = 65
CHART_SIZE_X = 24
CHART_SIZE_Y = 8
MTD_CARD_ID = 161
MTD_ROW = 73
KPI_ROW_1 = 78
KPI_ROW_2 = 82
TABLE_CARD_ID = 164
TABLE_ROW = 93

SQL = """WITH RECURSIVE DateRange AS (
    SELECT DATE_SUB(CURDATE(), INTERVAL 1 YEAR) AS date_point
    UNION ALL
    SELECT DATE_ADD(date_point, INTERVAL 1 DAY)
    FROM DateRange
    WHERE date_point < CURDATE()
),
InvoiceLevelSmartQty AS (
    SELECT
        si.name AS invoice_id,
        si.posting_date,
        CASE
            WHEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services'
                     AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            ) > 0
            THEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services'
                     AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
            ELSE MAX(
                CASE
                    WHEN sii.item_group = 'Grooming Services'
                     AND sii.item_code LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
        END AS smart_qty
    FROM `tabSales Invoice` si
    JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
    WHERE si.docstatus = 1
      AND (
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
        OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
      )
    GROUP BY si.name, si.posting_date
),
DailyGroomings AS (
    SELECT posting_date, SUM(smart_qty) AS daily_groomings
    FROM InvoiceLevelSmartQty
    WHERE smart_qty > 0
    GROUP BY posting_date
),
RollingMetrics AS (
    SELECT
        dr.date_point AS `Date`,
        (
            SELECT COUNT(DISTINCT si.customer)
            FROM `tabSales Invoice` si
            WHERE si.docstatus = 1
              AND (
                {{channel}} = 'Both'
                OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
                OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
              )
              AND si.posting_date BETWEEN DATE_SUB(dr.date_point, INTERVAL 30 DAY) AND dr.date_point
        ) AS `Active Customers (30D)`,
        COALESCE((
            SELECT SUM(dg.daily_groomings)
            FROM DailyGroomings dg
            WHERE dg.posting_date BETWEEN DATE_SUB(dr.date_point, INTERVAL 30 DAY) AND dr.date_point
        ), 0) AS `Groomings (30D)`
    FROM DateRange dr
)
SELECT `Date`, `Active Customers (30D)`, `Groomings (30D)`
FROM RollingMetrics
WHERE [[{{date_filter}}]]
ORDER BY `Date` ASC;"""

TEMPLATE_TAGS = {
    "date_filter": {
        "id": DATE_FILTER_ID,
        "name": "date_filter",
        "display-name": "Date",
        "type": "dimension",
        "widget-type": "date/range",
        "dimension": ["field", {"lib/uuid": str(uuid.uuid4())}, 15000],
        "alias": "Date",
        "default": "past12months~",
    },
    "channel": {
        "id": CHANNEL_ID,
        "name": "channel",
        "display-name": "Channel",
        "type": "text",
        "default": "Both",
    },
}

CARD_PARAMETERS = [
    {
        "id": DATE_FILTER_ID,
        "type": "date/range",
        "target": ["dimension", ["template-tag", "date_filter"]],
        "name": "Date",
        "slug": "rolling_date",
        "default": "past12months~",
        "isMultiSelect": True,
    },
    {
        "id": CHANNEL_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "channel"]],
        "name": "Channel",
        "slug": "channel",
        "default": "Both",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [["Both"], ["Store"], ["Truck"]]},
    },
]

VIZ = {
    "graph.dimensions": ["Date"],
    "graph.metrics": ["Active Customers (30D)", "Groomings (30D)"],
    "graph.x_axis.scale": "timeseries",
    "graph.x_axis.title_text": "Date",
    "graph.y_axis.title_text": "",
    "graph.y_axis.auto_split": True,
    "graph.show_values": False,
    "series_settings": {
        "Active Customers (30D)": {
            "color": "#509EE3",
            "title": "Active customers (30D)",
            "display": "line",
            "line.style": "solid",
            "axis": "left",
        },
        "Groomings (30D)": {
            "color": "#88BF4D",
            "title": "Groomings (30D)",
            "display": "line",
            "line.style": "solid",
            "axis": "right",
        },
    },
    "column_settings": {
        '["name","Groomings (30D)"]': {"decimals": 0},
        '["name","Active Customers (30D)"]': {"decimals": 0},
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
        "database": DATABASE_ID,
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
            f"visualization_settings = '{esc(json.dumps(VIZ))}', display = 'line', updated_at = NOW() "
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
    'Rolling 30-day active customers and groomings on dual axes',
    'line', '{esc(json.dumps(dq))}', '{esc(json.dumps(VIZ))}',
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


def place_on_dashboard(card_id: int) -> None:
    mappings = "[]"
    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    if existing:
        psql(
            f"UPDATE report_dashboardcard SET row = {CHART_ROW}, col = 0, "
            f"size_x = {CHART_SIZE_X}, size_y = {CHART_SIZE_Y}, "
            f"parameter_mappings = '{mappings}', updated_at = NOW() WHERE id = {existing};"
        )
    else:
        psql(
            f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id, inline_parameters
) VALUES (
    {CHART_SIZE_X}, {CHART_SIZE_Y}, {CHART_ROW}, 0, {card_id}, {DASHBOARD_ID},
    '{mappings}', '{{}}', '{entity_id()}', '{esc(json.dumps([CHANNEL_ID]))}'
);
"""
        )

    psql(
        f"UPDATE report_dashboardcard SET row = {MTD_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {MTD_CARD_ID};"
    )
    psql(
        f"UPDATE report_dashboardcard SET row = {KPI_ROW_1}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id BETWEEN 165 AND 168;"
    )
    psql(
        f"UPDATE report_dashboardcard SET row = {KPI_ROW_2}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id BETWEEN 169 AND 172;"
    )
    psql(
        f"UPDATE report_dashboardcard SET row = {TABLE_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {TABLE_CARD_ID};"
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
