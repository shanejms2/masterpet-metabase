#!/usr/bin/env python3
"""Monthly new customer acquisition chart for dashboard 37."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_ID = 181
CARD_NAME = "Monthly New Customers"

CHANNEL_ID = "b9e4d3c2-5a6f-7b8c-9d0e-1f2a3b4c5d6e"
DATE_FILTER_ID = "f1a2b3c4-d5e6-4f7a-8b9c-0d1e2f3a4b5c"

CHART_ROW = 57
CHART_SIZE_X = 24
CHART_SIZE_Y = 8

ROLLING_CARD_ID = 173
ROLLING_ROW = 65
MTD_CARD_ID = 161
MTD_ROW = 73

CURRENT_MONTH_CUTOFF = """(
      DATE_FORMAT(first_visit_date, '%Y-%m-01') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
      OR first_visit_date < CURDATE()
    )"""

SQL = """WITH FirstVisits AS (
    SELECT
        si.customer,
        MIN(si.posting_date) AS first_visit_date
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
      AND si.base_grand_total > 0
      AND (
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
        OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
      )
    GROUP BY si.customer
),
MonthlyAcquisition AS (
    SELECT
        DATE_FORMAT(first_visit_date, '%Y-%m-01') AS month_start,
        COUNT(*) AS new_customers
    FROM FirstVisits
    WHERE """ + CURRENT_MONTH_CUTOFF + """
    GROUP BY DATE_FORMAT(first_visit_date, '%Y-%m-01')
),
MonthlyTrend AS (
    SELECT
        month_start AS `Month`,
        new_customers AS `New Customers`,
        SUM(new_customers) OVER (ORDER BY month_start) AS `Cumulative Customers`
    FROM MonthlyAcquisition
)
SELECT `Month`, `New Customers`, `Cumulative Customers`
FROM MonthlyTrend
WHERE [[{{date_filter}}]]
ORDER BY `Month` ASC;"""

TEMPLATE_TAGS = {
    "date_filter": {
        "id": DATE_FILTER_ID,
        "name": "date_filter",
        "display-name": "Month",
        "type": "dimension",
        "widget-type": "date/range",
        "dimension": ["field", {"lib/uuid": str(uuid.uuid4())}, 15000],
        "alias": "Month",
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
        "name": "Month",
        "slug": "new_customers_month",
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
    "graph.dimensions": ["Month"],
    "graph.metrics": ["New Customers", "Cumulative Customers"],
    "graph.x_axis.scale": "timeseries",
    "graph.x_axis.title_text": "Month",
    "graph.y_axis.title_text": "",
    "graph.y_axis.auto_split": True,
    "graph.show_values": False,
    "series_settings": {
        "New Customers": {
            "color": "#509EE3",
            "title": "New customers",
            "display": "bar",
            "axis": "left",
        },
        "Cumulative Customers": {
            "color": "#88BF4D",
            "title": "Cumulative customers",
            "display": "line",
            "line.style": "solid",
            "axis": "right",
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


def upsert_card() -> int:
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
            f"description = 'New customers per month (first paid sales invoice; excludes ₹0) with cumulative total.', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(CARD_PARAMETERS))}', "
            f"visualization_settings = '{esc(json.dumps(VIZ))}', display = 'combo', "
            f"cache_invalidated_at = NOW(), updated_at = NOW() WHERE id = {CARD_ID};"
        )
        print(f"Updated card {CARD_ID}")
        return CARD_ID

    now = datetime.now(timezone.utc).isoformat()
    psql(
        f"""
INSERT INTO report_card (
    id, created_at, updated_at, name, description, display, dataset_query,
    visualization_settings, creator_id, database_id, query_type, collection_id,
    parameters, card_schema, type, entity_id, last_used_at
) VALUES (
    {CARD_ID}, '{now}', '{now}', '{esc(CARD_NAME)}',
            'New customers per month (first paid sales invoice; excludes ₹0) with cumulative total.',
    'combo', '{esc(json.dumps(dq))}', '{esc(json.dumps(VIZ))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
);
"""
    )
    print(f"Created card {CARD_ID}")
    return CARD_ID


def place_on_dashboard(card_id: int) -> None:
    mappings = json.dumps(
        [
            {
                "parameter_id": CHANNEL_ID,
                "card_id": card_id,
                "target": ["variable", ["template-tag", "channel"]],
            },
        ]
    )
    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    if existing:
        psql(
            f"UPDATE report_dashboardcard SET row = {CHART_ROW}, col = 0, "
            f"size_x = {CHART_SIZE_X}, size_y = {CHART_SIZE_Y}, "
            f"parameter_mappings = '{esc(mappings)}', updated_at = NOW() WHERE id = {existing};"
        )
    else:
        psql(
            f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id, inline_parameters
) VALUES (
    {CHART_SIZE_X}, {CHART_SIZE_Y}, {CHART_ROW}, 0, {card_id}, {DASHBOARD_ID},
    '{esc(mappings)}', '{{}}', '{entity_id()}', '{esc(json.dumps([DATE_FILTER_ID]))}'
);
"""
        )

    psql(
        f"UPDATE report_dashboardcard SET row = {ROLLING_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {ROLLING_CARD_ID};"
    )
    psql(
        f"UPDATE report_dashboardcard SET row = {MTD_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {MTD_CARD_ID};"
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
