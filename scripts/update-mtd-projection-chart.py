#!/usr/bin/env python3
"""MTD Sales vs Projection chart + projected month-end scalar (card 186)."""

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
CHART_CARD_ID = 158
SCALAR_CARD_ID = 186

MTD_START = "DATE_FORMAT(CURDATE(), '%Y-%m-01')"
MTD_END_EXCLUSIVE = "CURDATE()"
DAYS_ELAPSED = (
    f"GREATEST(DATEDIFF({MTD_END_EXCLUSIVE} - INTERVAL 1 DAY, {MTD_START}) + 1, 1)"
)
DAYS_IN_MONTH = "DAY(LAST_DAY(CURDATE()))"

CHART_SQL = f"""
WITH RECURSIVE MonthDays AS (
    SELECT {MTD_START} AS day_date, 1 AS day_num
    UNION ALL
    SELECT DATE_ADD(day_date, INTERVAL 1 DAY), day_num + 1
    FROM MonthDays
    WHERE day_date < LAST_DAY(CURDATE())
),
DailyActual AS (
    SELECT
        si.posting_date AS day_date,
        SUM(si.base_grand_total) AS daily_gross
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
    GROUP BY si.posting_date
),
MonthStats AS (
    SELECT SUM(si.base_grand_total) / {DAYS_ELAPSED} AS avg_daily
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
)
SELECT
    md.day_date AS date,
    CASE
        WHEN md.day_date < {MTD_END_EXCLUSIVE} THEN
            ROUND(
                SUM(COALESCE(da.daily_gross, 0)) OVER (ORDER BY md.day_date) / 100000,
                2
            )
        ELSE NULL
    END AS `Actual`,
    ROUND((ms.avg_daily * md.day_num) / 100000, 2) AS `Projected pace`
FROM MonthDays md
CROSS JOIN MonthStats ms
LEFT JOIN DailyActual da ON da.day_date = md.day_date
ORDER BY md.day_date ASC;"""

SCALAR_SQL = f"""
WITH MonthStats AS (
    SELECT SUM(si.base_grand_total) / {DAYS_ELAPSED} AS avg_daily
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
)
SELECT {_top.indian_rupee_sql(f'ms.avg_daily * {DAYS_IN_MONTH}')} AS `Value`
FROM MonthStats ms;"""

CHART_VIZ = {
    "graph.dimensions": ["date"],
    "graph.metrics": ["Actual", "Projected pace"],
    "graph.x_axis.scale": "timeseries",
    "graph.x_axis.title_text": "",
    "graph.y_axis.title_text": "₹ Lakhs (incl. tax)",
    "graph.show_values": False,
    "graph.show_legend": True,
    "series_settings": {
        "Actual": {
            "color": "#509EE3",
            "title": "Actual (MTD)",
            "line.marker_enabled": True,
            "line.size": "M",
        },
        "Projected pace": {
            "color": "#88BF4D",
            "title": "Projected pace",
            "line.style": "dashed",
            "line.marker_enabled": False,
            "line.size": "M",
        },
    },
    "column_settings": {
        '["name","Actual"]': {"prefix": "₹", "suffix": " L", "decimals": 2},
        '["name","Projected pace"]': {"prefix": "₹", "suffix": " L", "decimals": 2},
    },
}

SCALAR_VIZ = {
    "scalar.field": "Value",
    "scalar.compact_primary_number": True,
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
        "MTD Sales vs Projection",
        "Cumulative actual through yesterday vs projected month-end pace",
        "line",
        CHART_SQL,
        CHART_VIZ,
    )
    upsert_card(
        SCALAR_CARD_ID,
        "Projected Month-end Sales",
        "MTD daily average × days in month (incl. tax)",
        "scalar",
        SCALAR_SQL,
        SCALAR_VIZ,
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
