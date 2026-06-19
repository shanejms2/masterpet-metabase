#!/usr/bin/env python3
"""Top-of-dashboard KPIs: follow-ups by agent, overdue, MTD sales & groomings."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import (
    agent_emails_sql,
    agent_names_field_order,
    agents_union_sql,
)

_spec = importlib.util.spec_from_file_location(
    "kpi",
    Path(__file__).with_name("add-followup-kpi-cards.py"),
)
_kpi = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_kpi)

_spec = importlib.util.spec_from_file_location(
    "today_fups",
    Path(__file__).with_name("add-todays-followups-table.py"),
)
_today_fups = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_today_fups)

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = _kpi.DASHBOARD_ID
CARD_SCHEMA = 23

FOLLOWUP_DAY_ID = _today_fups.FOLLOWUP_DAY_ID
FOLLOWUP_DAY_TAGS = _today_fups.TEMPLATE_TAGS
FOLLOWUP_DAY_PARAMS = _today_fups.CARD_PARAMETERS

# Layout is managed by reorganize-dashboard-37.py

AGENT_EMAILS_SQL = agent_emails_sql()

MTD_START = "DATE_FORMAT(CURDATE(), '%Y-%m-01')"
MTD_END_EXCLUSIVE = "CURDATE()"
# Sales MTD excludes today (incomplete day); follow-up logs include today.
MTD_FOLLOWUP_END_EXCLUSIVE = "DATE_ADD(CURDATE(), INTERVAL 1 DAY)"
DAYS_ELAPSED = (
    f"GREATEST(DATEDIFF({MTD_END_EXCLUSIVE} - INTERVAL 1 DAY, {MTD_START}) + 1, 1)"
)
FOLLOWUP_DAYS_ELAPSED = (
    f"GREATEST(DATEDIFF(CURDATE(), {MTD_START}) + 1, 1)"
)


def indian_rupee_sql(amount_sql: str) -> str:
    """Format a numeric SQL expression as ₹ with Indian digit grouping."""
    amount = f"CAST(ROUND({amount_sql}) AS SIGNED)"
    return f"""CONCAT('₹',
        CASE
            WHEN {amount} < 1000 THEN CAST({amount} AS CHAR)
            WHEN {amount} < 100000 THEN CONCAT(
                CAST(FLOOR({amount} / 1000) AS CHAR), ',',
                LPAD(MOD({amount}, 1000), 3, '0')
            )
            WHEN {amount} < 10000000 THEN CONCAT(
                CAST(FLOOR({amount} / 100000) AS CHAR), ',',
                LPAD(FLOOR(MOD({amount}, 100000) / 1000), 2, '0'), ',',
                LPAD(MOD({amount}, 1000), 3, '0')
            )
            ELSE CONCAT(
                CAST(FLOOR({amount} / 10000000) AS CHAR), ',',
                LPAD(FLOOR(MOD({amount}, 10000000) / 100000), 2, '0'), ',',
                LPAD(FLOOR(MOD({amount}, 100000) / 1000), 2, '0'), ',',
                LPAD(MOD({amount}, 1000), 3, '0')
            )
        END
    )"""


FOLLOWUPS_MTD_SQL = f"""
SELECT
    agents.agent_name AS `Agent`,
    COALESCE(logged.cnt, 0) AS `Follow-ups`
FROM (
    {agents_union_sql()}
) agents
INNER JOIN (
    SELECT cfu.agent, COUNT(*) AS cnt
    FROM `tabCustomer Follow Ups` cfu
    WHERE cfu.agent IN ({AGENT_EMAILS_SQL})
      AND DATE(cfu.creation) >= {MTD_START}
      AND DATE(cfu.creation) < {MTD_FOLLOWUP_END_EXCLUSIVE}
    GROUP BY cfu.agent
) logged ON logged.agent = agents.email
ORDER BY FIELD(agents.agent_name, {agent_names_field_order()});"""

FOLLOWUPS_MTD_TOTAL_SQL = f"""
SELECT COUNT(*) AS `Count`
FROM `tabCustomer Follow Ups` cfu
WHERE cfu.agent IN ({AGENT_EMAILS_SQL})
  AND DATE(cfu.creation) >= {MTD_START}
  AND DATE(cfu.creation) < {MTD_FOLLOWUP_END_EXCLUSIVE};"""

FOLLOWUPS_TODAY_SQL = f"""
SELECT COUNT(*) AS `Count`
FROM `tabCustomer Follow Ups` cfu
WHERE cfu.agent IN ({AGENT_EMAILS_SQL})
  AND DATE(cfu.creation) = [[{{{{followup_day}}}} --]] CURDATE();"""

FOLLOWUPS_AVG_DAILY_SQL = f"""
SELECT ROUND(COUNT(*) / {FOLLOWUP_DAYS_ELAPSED}, 1) AS `Count`
FROM `tabCustomer Follow Ups` cfu
WHERE cfu.agent IN ({AGENT_EMAILS_SQL})
  AND DATE(cfu.creation) >= {MTD_START}
  AND DATE(cfu.creation) < {MTD_FOLLOWUP_END_EXCLUSIVE};"""

OVERDUE_SQL = (
    _kpi.BASE_CTES
    + """
SELECT COUNT(*) AS `Count`
FROM FollowUpQueue
WHERE call_priority = 'Overdue';"""
)

MTD_REVENUE_SCALAR_SQL = f"""
WITH InvoiceLines AS (
    SELECT
        sii.base_amount
            + (si.base_grand_total - SUM(sii.base_amount) OVER (PARTITION BY si.name))
              * (sii.base_amount / NULLIF(SUM(sii.base_amount) OVER (PARTITION BY si.name), 0)) AS gross_with_tax
    FROM `tabSales Invoice` si
    INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
),
Totals AS (
    SELECT SUM(gross_with_tax) AS total_amt FROM InvoiceLines
)
SELECT {indian_rupee_sql('total_amt')} AS `Value` FROM Totals;"""

MTD_GROOMINGS_SCALAR_SQL = f"""
WITH InvoiceGroomings AS (
    SELECT
        si.name AS invoice_id,
        CASE
            WHEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            ) > 0
            THEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
            ELSE MAX(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
        END AS smart_qty
    FROM `tabSales Invoice` si
    JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND sii.item_group IN ('Grooming Services', 'Travel Services')
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
    GROUP BY si.name
)
SELECT CAST(COALESCE(SUM(smart_qty), 0) AS UNSIGNED) AS `Count`
FROM InvoiceGroomings
WHERE smart_qty > 0;"""

AVG_DAILY_REVENUE_SCALAR_SQL = f"""
SELECT {indian_rupee_sql(f'SUM(si.base_grand_total) / {DAYS_ELAPSED}')} AS `Value`
FROM `tabSales Invoice` si
WHERE si.docstatus = 1
  AND si.posting_date >= {MTD_START}
  AND si.posting_date < {MTD_END_EXCLUSIVE};"""

REVENUE_MTD_SQL = f"""
WITH InvoiceLines AS (
    SELECT
        CASE
            WHEN si.pos_profile = 'Vennala POS' THEN 'Store'
            ELSE 'Truck'
        END AS channel,
        CASE
            WHEN sii.item_group IN ('Grooming Services', 'Travel Services') THEN 'Grooming & Travel'
            ELSE 'Product'
        END AS category,
        sii.base_amount
            + (si.base_grand_total - SUM(sii.base_amount) OVER (PARTITION BY si.name))
              * (sii.base_amount / NULLIF(SUM(sii.base_amount) OVER (PARTITION BY si.name), 0)) AS gross_with_tax
    FROM `tabSales Invoice` si
    INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
),
CategoryChannel AS (
    SELECT
        category,
        ROUND(SUM(CASE WHEN channel = 'Store' THEN gross_with_tax ELSE 0 END), 0) AS store_amt,
        ROUND(SUM(CASE WHEN channel = 'Truck' THEN gross_with_tax ELSE 0 END), 0) AS truck_amt,
        ROUND(SUM(gross_with_tax), 0) AS total_amt
    FROM InvoiceLines
    GROUP BY category
)
SELECT
    category AS `Category`,
    {indian_rupee_sql('store_amt')} AS `Store`,
    {indian_rupee_sql('truck_amt')} AS `Truck`,
    {indian_rupee_sql('total_amt')} AS `Total`
FROM CategoryChannel
UNION ALL
SELECT
    'Total',
    {indian_rupee_sql('SUM(store_amt)')},
    {indian_rupee_sql('SUM(truck_amt)')},
    {indian_rupee_sql('SUM(total_amt)')}
FROM CategoryChannel
ORDER BY FIELD(`Category`, 'Grooming & Travel', 'Product', 'Total');"""

GROOMING_MTD_SQL = f"""
WITH InvoiceGroomings AS (
    SELECT
        si.name AS invoice_id,
        CASE WHEN si.pos_profile = 'Vennala POS' THEN 'Store' ELSE 'Truck' END AS channel,
        CASE
            WHEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            ) > 0
            THEN SUM(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code NOT LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
            ELSE MAX(
                CASE
                    WHEN sii.item_group = 'Grooming Services' AND sii.item_code LIKE '%ADD%'
                    THEN sii.qty ELSE 0
                END
            )
        END AS smart_qty
    FROM `tabSales Invoice` si
    JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND sii.item_group IN ('Grooming Services', 'Travel Services')
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
    GROUP BY si.name, channel
),
GroomingByChannel AS (
    SELECT channel, SUM(smart_qty) AS mtd_groomings
    FROM InvoiceGroomings
    WHERE smart_qty > 0
    GROUP BY channel
),
InvoiceLines AS (
    SELECT
        CASE WHEN si.pos_profile = 'Vennala POS' THEN 'Store' ELSE 'Truck' END AS channel,
        CASE
            WHEN sii.item_group IN ('Grooming Services', 'Travel Services') THEN 'Grooming & Travel'
            ELSE 'Product'
        END AS category,
        sii.base_amount
            + (si.base_grand_total - SUM(sii.base_amount) OVER (PARTITION BY si.name))
              * (sii.base_amount / NULLIF(SUM(sii.base_amount) OVER (PARTITION BY si.name), 0)) AS gross_with_tax
    FROM `tabSales Invoice` si
    INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
),
GroomingRevenueByChannel AS (
    SELECT channel, SUM(gross_with_tax) AS mtd_grooming_revenue
    FROM InvoiceLines
    WHERE category = 'Grooming & Travel'
    GROUP BY channel
),
ChannelGroomingStats AS (
    SELECT
        g.channel,
        g.mtd_groomings,
        COALESCE(gr.mtd_grooming_revenue, 0) AS mtd_grooming_revenue
    FROM GroomingByChannel g
    LEFT JOIN GroomingRevenueByChannel gr ON gr.channel = g.channel
),
SalesByChannel AS (
    SELECT
        CASE WHEN si.pos_profile = 'Vennala POS' THEN 'Store' ELSE 'Truck' END AS channel,
        SUM(si.base_grand_total) AS mtd_sales
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
      AND si.posting_date >= {MTD_START}
      AND si.posting_date < {MTD_END_EXCLUSIVE}
    GROUP BY channel
),
Days AS (
    SELECT {DAYS_ELAPSED} AS days_elapsed
),
GroomingMetrics AS (
    SELECT
        'MTD Groomings' AS metric_name,
        CAST(COALESCE(MAX(CASE WHEN g.channel = 'Store' THEN g.mtd_groomings END), 0) AS UNSIGNED) AS store_val,
        CAST(COALESCE(MAX(CASE WHEN g.channel = 'Truck' THEN g.mtd_groomings END), 0) AS UNSIGNED) AS truck_val,
        CAST(COALESCE(SUM(g.mtd_groomings), 0) AS UNSIGNED) AS total_val,
        'count' AS value_kind
    FROM GroomingByChannel g
    UNION ALL
    SELECT
        'Avg Daily Groomings',
        ROUND(COALESCE(MAX(CASE WHEN g.channel = 'Store' THEN g.mtd_groomings END), 0) / d.days_elapsed, 1),
        ROUND(COALESCE(MAX(CASE WHEN g.channel = 'Truck' THEN g.mtd_groomings END), 0) / d.days_elapsed, 1),
        ROUND(COALESCE(SUM(g.mtd_groomings), 0) / d.days_elapsed, 1),
        'count'
    FROM GroomingByChannel g
    CROSS JOIN Days d
    UNION ALL
    SELECT
        'Avg Daily Grooming Revenue',
        ROUND(COALESCE(MAX(CASE WHEN gr.channel = 'Store' THEN gr.mtd_grooming_revenue END), 0) / d.days_elapsed, 0),
        ROUND(COALESCE(MAX(CASE WHEN gr.channel = 'Truck' THEN gr.mtd_grooming_revenue END), 0) / d.days_elapsed, 0),
        ROUND(COALESCE(SUM(gr.mtd_grooming_revenue), 0) / d.days_elapsed, 0),
        'rupee'
    FROM GroomingRevenueByChannel gr
    CROSS JOIN Days d
    UNION ALL
    SELECT
        'MTD Avg Revenue per Grooming',
        ROUND(
            COALESCE(MAX(CASE WHEN c.channel = 'Store' THEN c.mtd_grooming_revenue END), 0)
            / NULLIF(COALESCE(MAX(CASE WHEN c.channel = 'Store' THEN c.mtd_groomings END), 0), 0),
            0
        ),
        ROUND(
            COALESCE(MAX(CASE WHEN c.channel = 'Truck' THEN c.mtd_grooming_revenue END), 0)
            / NULLIF(COALESCE(MAX(CASE WHEN c.channel = 'Truck' THEN c.mtd_groomings END), 0), 0),
            0
        ),
        ROUND(
            COALESCE(SUM(c.mtd_grooming_revenue), 0)
            / NULLIF(COALESCE(SUM(c.mtd_groomings), 0), 0),
            0
        ),
        'rupee'
    FROM ChannelGroomingStats c
    UNION ALL
    SELECT
        'Avg Daily Revenue (incl. tax)',
        ROUND(COALESCE(MAX(CASE WHEN s.channel = 'Store' THEN s.mtd_sales END), 0) / d.days_elapsed, 0),
        ROUND(COALESCE(MAX(CASE WHEN s.channel = 'Truck' THEN s.mtd_sales END), 0) / d.days_elapsed, 0),
        ROUND(COALESCE(SUM(s.mtd_sales), 0) / d.days_elapsed, 0),
        'rupee'
    FROM SalesByChannel s
    CROSS JOIN Days d
)
SELECT
    metric_name AS `Metric`,
    CASE
        WHEN value_kind = 'rupee' THEN {indian_rupee_sql('store_val')}
        ELSE CAST(store_val AS CHAR)
    END AS `Store`,
    CASE
        WHEN value_kind = 'rupee' THEN {indian_rupee_sql('truck_val')}
        ELSE CAST(truck_val AS CHAR)
    END AS `Truck`,
    CASE
        WHEN value_kind = 'rupee' THEN {indian_rupee_sql('total_val')}
        ELSE CAST(total_val AS CHAR)
    END AS `Total`
FROM GroomingMetrics;"""

CARDS = [
    {
        "id": 183,
        "name": "MTD Revenue",
        "description": "Month-to-date gross sales incl. tax",
        "display": "scalar",
        "sql": MTD_REVENUE_SCALAR_SQL,
        "tags": {},
        "params": [],
        "viz": {"scalar.field": "Value", "scalar.compact_primary_number": True},
    },
    {
        "id": 184,
        "name": "MTD Groomings",
        "description": "Month-to-date grooming count",
        "display": "scalar",
        "sql": MTD_GROOMINGS_SCALAR_SQL,
        "tags": {},
        "params": [],
        "viz": {"scalar.field": "Count", "scalar.compact_primary_number": True},
    },
    {
        "id": 185,
        "name": "Avg Daily Revenue",
        "description": "Average daily gross sales incl. tax this month",
        "display": "scalar",
        "sql": AVG_DAILY_REVENUE_SCALAR_SQL,
        "tags": {},
        "params": [],
        "viz": {"scalar.field": "Value", "scalar.compact_primary_number": True},
    },
    {
        "id": 176,
        "name": "MTD Follow-ups by Agent",
        "description": "Follow-ups logged this month by agent",
        "display": "table",
        "sql": FOLLOWUPS_MTD_SQL,
        "tags": {},
        "params": [],
        "viz": {
            "table.pivot": False,
            "column_settings": {
                '["name","Follow-ups"]': {"number_separators": ","},
            },
        },
    },
    {
        "id": 177,
        "name": "MTD Followups Completed",
        "description": "Follow-ups logged this month",
        "display": "scalar",
        "sql": FOLLOWUPS_MTD_TOTAL_SQL,
        "tags": {},
        "params": [],
        "viz": {
            "scalar.field": "Count",
            "scalar.compact_primary_number": True,
        },
    },
    {
        "id": 190,
        "name": "Followups Done Today",
        "description": "Follow-ups for the selected day (defaults to today)",
        "display": "scalar",
        "sql": FOLLOWUPS_TODAY_SQL,
        "tags": FOLLOWUP_DAY_TAGS,
        "params": FOLLOWUP_DAY_PARAMS,
        "viz": {
            "scalar.field": "Count",
            "scalar.compact_primary_number": True,
        },
    },
    {
        "id": 182,
        "name": "Avg Daily Followups Completed",
        "description": "Average follow-ups logged per day this month",
        "display": "scalar",
        "sql": FOLLOWUPS_AVG_DAILY_SQL,
        "tags": {},
        "params": [],
        "viz": {
            "scalar.field": "Count",
            "scalar.compact_primary_number": True,
        },
    },
    {
        "id": 178,
        "name": "Overdue Followups",
        "description": "Customers past their follow-up date",
        "display": "scalar",
        "sql": OVERDUE_SQL,
        "tags": _kpi.TEMPLATE_TAGS,
        "params": _kpi.CARD_PARAMETERS,
        "viz": {
            "scalar.field": "Count",
            "scalar.compact_primary_number": True,
        },
    },
    {
        "id": 179,
        "name": "MTD Revenue by Channel & Category",
        "description": "Month-to-date gross sales incl. tax (₹)",
        "display": "table",
        "sql": REVENUE_MTD_SQL,
        "tags": {},
        "params": [],
        "viz": {
            "table.pivot": False,
        },
    },
    {
        "id": 180,
        "name": "MTD Groomings & Daily Averages",
        "description": "MTD groomings and daily averages by channel (counts, revenue, and revenue per grooming)",
        "display": "table",
        "sql": GROOMING_MTD_SQL,
        "tags": {},
        "params": [],
        "viz": {
            "table.pivot": False,
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


def upsert_card(spec: dict) -> int:
    card_id = spec["id"]
    existing = psql(f"SELECT id FROM report_card WHERE id = {card_id} LIMIT 1;")
    dq = {
        "lib/type": "mbql/query",
        "database": DATABASE_ID,
        "stages": [
            {
                "lib/type": "mbql.stage/native",
                "native": spec["sql"],
                "template-tags": spec["tags"],
            }
        ],
    }
    if existing:
        psql(
            f"UPDATE report_card SET name = '{esc(spec['name'])}', "
            f"description = '{esc(spec['description'])}', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(spec['params']))}', "
            f"visualization_settings = '{esc(json.dumps(spec['viz']))}', "
            f"display = '{esc(spec['display'])}', updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}: {spec['name']}")
        return card_id

    now = datetime.now(timezone.utc).isoformat()
    psql(
        f"""
INSERT INTO report_card (
    id, created_at, updated_at, name, description, display, dataset_query,
    visualization_settings, creator_id, database_id, query_type, collection_id,
    parameters, card_schema, type, entity_id, last_used_at
) VALUES (
    {card_id}, '{now}', '{now}', '{esc(spec['name'])}', '{esc(spec['description'])}',
    '{esc(spec['display'])}', '{esc(json.dumps(dq))}', '{esc(json.dumps(spec['viz']))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(spec['params']))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
);
"""
    )
    print(f"Created card {card_id}: {spec['name']}")
    return card_id


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
