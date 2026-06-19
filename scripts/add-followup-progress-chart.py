#!/usr/bin/env python3
"""Follow-up overdue trend chart: daily overdue count over time."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import agent_emails_sql, agent_logged_cols_sql, agent_tuples

_spec = importlib.util.spec_from_file_location(
    "kpi",
    Path(__file__).with_name("add-followup-kpi-cards.py"),
)
_kpi = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_kpi)

_spec2 = importlib.util.spec_from_file_location(
    "upd",
    Path(__file__).with_name("update-followup-table.py"),
)
_upd = importlib.util.module_from_spec(_spec2)
assert _spec2.loader is not None
_spec2.loader.exec_module(_upd)

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = _upd.DASHBOARD_ID
CARD_SCHEMA = 23
CARD_NAME = "Follow-up Overdue vs Done"

CHART_ROW = 86
CHART_SIZE_X = 24
CHART_SIZE_Y = 7
TABLE_CARD_ID = 164
TABLE_ROW = 93
AGENT_CARD_ID = 174
AGENT_ROW = 103

CHART_DAYS = 30
CHART_START_SQL = f"DATE_SUB(CURDATE(), INTERVAL {CHART_DAYS} DAY)"

AGENTS = agent_tuples()
AGENT_EMAILS_SQL = agent_emails_sql()
AGENT_LOGGED_COLS = agent_logged_cols_sql()
AGENT_TREND_COLS = ",\n        ".join(
    f"COALESCE(dl.`{name}`, 0) AS `{name}`" for name, _ in AGENTS
)
AGENT_METRICS = [name for name, _ in AGENTS]

_BASE_PREFIX = _kpi.BASE_CTES[: _kpi.BASE_CTES.index("AllChannelFood AS (")]

_CUSTOMER_FILTERS = """(
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND ch.has_store = 1)
        OR ({{channel}} = 'Truck' AND ch.has_truck = 1)
      )
      [[AND (
        {{purchase_profile}} = 'All'
        OR ({{purchase_profile}} = 'Grooming only'
            AND COALESCE(cp.has_grooming, 0) = 1
            AND COALESCE(cp.has_products, 0) = 0)
        OR ({{purchase_profile}} = 'Products only'
            AND COALESCE(cp.has_grooming, 0) = 0
            AND COALESCE(cp.has_products, 0) = 1)
        OR ({{purchase_profile}} = 'Grooming & Products'
            AND COALESCE(cp.has_grooming, 0) = 1
            AND COALESCE(cp.has_products, 0) = 1)
      )]]
      AND (
        {{cohort}} = 'All'
        OR ({{cohort}} = 'Dormant (45-90 days)'
            AND acg.last_grooming_visit BETWEEN DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                AND DATE_SUB(CURDATE(), INTERVAL 45 DAY))
        OR ({{cohort}} = 'Lost (90+ days)'
            AND acg.last_grooming_visit < DATE_SUB(CURDATE(), INTERVAL 90 DAY))
        OR ({{cohort}} = 'Dormant + Lost'
            AND acg.last_grooming_visit < DATE_SUB(CURDATE(), INTERVAL 45 DAY))
      )"""

SQL = (
    f"""WITH RECURSIVE DateRange AS (
    SELECT {CHART_START_SQL} AS date_point
    UNION ALL
    SELECT DATE_ADD(date_point, INTERVAL 1 DAY)
    FROM DateRange
    WHERE date_point < CURDATE()
),
"""
    + _BASE_PREFIX[len("WITH ") :]
    + f"""
FollowUpSpans AS (
    SELECT
        cfu.customer,
        DATE(cfu.creation) AS span_from,
        cfu.creation AS last_followup_date,
        cfu.next_follow_up,
        cfu.no_follow_up_reason,
        COALESCE(
            LEAD(DATE(cfu.creation)) OVER (
                PARTITION BY cfu.customer ORDER BY cfu.creation, cfu.name
            ),
            DATE_ADD(CURDATE(), INTERVAL 1 DAY)
        ) AS span_to_exclusive
    FROM `tabCustomer Follow Ups` cfu
    JOIN WorkQueueCustomers wq ON wq.customer = cfu.customer
),
OverdueActive AS (
    SELECT
        dr.date_point,
        fs.customer,
        fs.last_followup_date,
        fs.next_follow_up
    FROM FollowUpSpans fs
    JOIN DateRange dr
      ON dr.date_point >= GREATEST(fs.span_from, DATE_ADD(fs.next_follow_up, INTERVAL 1 DAY))
     AND dr.date_point < fs.span_to_exclusive
    WHERE fs.next_follow_up IS NOT NULL
      AND (fs.no_follow_up_reason IS NULL OR fs.no_follow_up_reason = '')
),
DailyOverdue AS (
    SELECT oa.date_point AS `Date`, COUNT(*) AS `Overdue`
    FROM OverdueActive oa
    JOIN CustomerChannels ch ON oa.customer = ch.customer
    LEFT JOIN CustomerPurchases cp ON oa.customer = cp.customer
    LEFT JOIN AllChannelGrooming acg ON oa.customer = acg.customer
    WHERE """
    + _CUSTOMER_FILTERS
    + """
      AND NOT EXISTS (
          SELECT 1
          FROM InvoiceRollup ir
          WHERE ir.customer = oa.customer
            AND ir.grooming_base_sum > 0
            AND ir.posting_date <= oa.date_point
            AND ir.posting_date >= oa.next_follow_up
            AND ir.posting_date >= DATE(oa.last_followup_date)
      )
    GROUP BY oa.date_point
),
"""
    + f"""
DailyLogged AS (
    SELECT
        DATE(cfu.creation) AS `Date`,
        {AGENT_LOGGED_COLS}
    FROM `tabCustomer Follow Ups` cfu
    JOIN WorkQueueCustomers wq ON wq.customer = cfu.customer
    JOIN CustomerChannels ch ON ch.customer = cfu.customer
    LEFT JOIN CustomerPurchases cp ON cfu.customer = cp.customer
    LEFT JOIN AllChannelGrooming acg ON acg.customer = cfu.customer
    WHERE cfu.agent IN ({AGENT_EMAILS_SQL})
      AND DATE(cfu.creation) >= {CHART_START_SQL}
      AND """
    + _CUSTOMER_FILTERS
    + f"""
    GROUP BY DATE(cfu.creation)
),
FollowUpTrend AS (
    SELECT
        dr.date_point AS `Date`,
        COALESCE(dover.`Overdue`, 0) AS `Overdue`,
        {AGENT_TREND_COLS}
    FROM DateRange dr
    LEFT JOIN DailyOverdue dover ON dover.`Date` = dr.date_point
    LEFT JOIN DailyLogged dl ON dl.`Date` = dr.date_point
)
SELECT `Date`, `Overdue`, """
    + ", ".join(f"`{name}`" for name in AGENT_METRICS)
    + f"""
FROM FollowUpTrend
ORDER BY `Date` ASC;"""
)

CACHE_TTL = 3600

TEMPLATE_TAGS = _kpi.TEMPLATE_TAGS

CARD_PARAMETERS = list(_kpi.CARD_PARAMETERS)

VIZ = {
    "graph.dimensions": ["Date"],
    "graph.metrics": [*AGENT_METRICS, "Overdue"],
    "graph.x_axis.scale": "timeseries",
    "graph.x_axis.title_text": "Date",
    "graph.y_axis.title_text": "",
    "graph.y_axis.auto_split": True,
    "graph.show_values": False,
    "stackable.stack_type": "stacked",
    "series_settings": {
        "Jishnu": {
            "color": "#509EE3",
            "title": "Jishnu",
            "display": "bar",
            "axis": "left",
        },
        "Sivagauri": {
            "color": "#88BF4D",
            "title": "Sivagauri",
            "display": "bar",
            "axis": "left",
        },
        "Shane": {
            "color": "#A989C5",
            "title": "Shane",
            "display": "bar",
            "axis": "left",
        },
        "Thomas": {
            "color": "#F9D45C",
            "title": "Thomas",
            "display": "bar",
            "axis": "left",
        },
        "Overdue": {
            "color": "#EF8C8C",
            "title": "Overdue",
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
    existing = psql(
        f"SELECT id FROM report_card WHERE id = 175 AND archived = false "
        f"UNION SELECT id FROM report_card WHERE name = '{esc(CARD_NAME)}' AND archived = false LIMIT 1;"
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
        card_id = int(existing.split("\n")[0])
        psql(
            f"UPDATE report_card SET name = '{esc(CARD_NAME)}', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(CARD_PARAMETERS))}', "
            f"description = 'Daily overdue backlog vs follow-ups logged per agent (past 30 days).', "
            f"visualization_settings = '{esc(json.dumps(VIZ))}', display = 'combo', "
            f"cache_ttl = {CACHE_TTL}, cache_invalidated_at = NOW(), updated_at = NOW() WHERE id = {card_id};"
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
    'Daily overdue backlog vs follow-ups logged by the team.',
    'combo', '{esc(json.dumps(dq))}', '{esc(json.dumps(VIZ))}',
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
    mappings = json.dumps(
        [
            {"parameter_id": _upd.CHANNEL_ID, "card_id": card_id, "target": ["variable", ["template-tag", "channel"]]},
            {"parameter_id": _upd.PROFILE_ID, "card_id": card_id, "target": ["variable", ["template-tag", "purchase_profile"]]},
            {"parameter_id": _upd.COHORT_ID, "card_id": card_id, "target": ["variable", ["template-tag", "cohort"]]},
            {"parameter_id": _upd.SHOW_CLOSED_WON_ID, "card_id": card_id, "target": ["variable", ["template-tag", "show_closed_won"]]},
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
    parameter_mappings, visualization_settings, entity_id
) VALUES (
    {CHART_SIZE_X}, {CHART_SIZE_Y}, {CHART_ROW}, 0, {card_id}, {DASHBOARD_ID},
    '{esc(mappings)}', '{{}}', '{entity_id()}'
);
"""
        )

    psql(
        f"UPDATE report_dashboardcard SET row = {TABLE_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {TABLE_CARD_ID};"
    )
    psql(
        f"UPDATE report_dashboardcard SET row = {AGENT_ROW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {AGENT_CARD_ID};"
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
