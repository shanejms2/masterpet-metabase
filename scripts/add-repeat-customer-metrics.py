#!/usr/bin/env python3
"""Repeat / returning customer metrics for dashboard 37 (cards 191–195)."""

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

CHANNEL_ID = "b9e4d3c2-5a6f-7b8c-9d0e-1f2a3b4c5d6e"
DATE_FILTER_ID = "d386a5dd-02d4-43aa-a5d1-20e0493590e9"
COHORT_DATE_FILTER_ID = "c191a001-0001-4000-8000-000000000191"
COHORT_MONTH_PARAM_ID = "c194a001-0001-4000-8000-000000000194"
MONTHS_SINCE_PARAM_ID = "c194a002-0001-4000-8000-000000000195"
DETAIL_CARD_ID = 194
COHORT_ROSTER_CARD_ID = 195
MAX_COHORT_MONTHS = 36
QUESTION_DRILL_PATH = "/question/194"

MTD_START = "DATE_FORMAT(CURDATE(), '%Y-%m-01')"
MTD_END_EXCLUSIVE = "CURDATE()"
# Past months = full month; current month = through yesterday (matches MTD revenue KPIs).
CURRENT_MONTH_CUTOFF = """(
      DATE_FORMAT(si.posting_date, '%Y-%m-01') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
      OR si.posting_date < CURDATE()
    )"""

CHANNEL_PREDICATE = """(
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
        OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
      )"""

INVOICE_BASE = f"""si.docstatus = 1
      AND si.base_grand_total > 0
      AND {CHANNEL_PREDICATE}"""

RETURNING_MTD_SQL = f"""
WITH FirstVisit AS (
    SELECT
        si.customer,
        MIN(si.posting_date) AS first_visit
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
    GROUP BY si.customer
)
SELECT COUNT(DISTINCT si.customer) AS `Count`
FROM `tabSales Invoice` si
INNER JOIN FirstVisit fv ON fv.customer = si.customer
WHERE {INVOICE_BASE}
  AND fv.first_visit < {MTD_START}
  AND si.posting_date >= {MTD_START}
  AND si.posting_date < {MTD_END_EXCLUSIVE};"""

MONTHLY_RETENTION_TREND_SQL = f"""
WITH FirstVisit AS (
    SELECT
        si.customer,
        MIN(si.posting_date) AS first_visit
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
    GROUP BY si.customer
),
MonthlyInvoices AS (
    SELECT
        si.customer,
        DATE_FORMAT(si.posting_date, '%Y-%m-01') AS month_start,
        COUNT(DISTINCT si.name) AS invoice_count
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
      AND {CURRENT_MONTH_CUTOFF}
    GROUP BY si.customer, DATE_FORMAT(si.posting_date, '%Y-%m-01')
),
MonthSeries AS (
    SELECT DISTINCT month_start FROM MonthlyInvoices
    UNION
    SELECT DISTINCT DATE_FORMAT(first_visit, '%Y-%m-01') FROM FirstVisit
),
ReturningByMonth AS (
    SELECT
        mi.month_start,
        COUNT(DISTINCT mi.customer) AS returning_customers
    FROM MonthlyInvoices mi
    INNER JOIN FirstVisit fv ON fv.customer = mi.customer
    WHERE fv.first_visit < mi.month_start
    GROUP BY mi.month_start
),
MultiVisitByMonth AS (
    SELECT
        month_start,
        COUNT(*) AS multi_visit_customers
    FROM MonthlyInvoices
    WHERE invoice_count >= 2
    GROUP BY month_start
),
NewByMonth AS (
    SELECT
        DATE_FORMAT(fv.first_visit, '%Y-%m-01') AS month_start,
        COUNT(*) AS new_customers
    FROM FirstVisit fv
    WHERE (
        DATE_FORMAT(fv.first_visit, '%Y-%m-01') < DATE_FORMAT(CURDATE(), '%Y-%m-01')
        OR fv.first_visit < CURDATE()
    )
    GROUP BY DATE_FORMAT(fv.first_visit, '%Y-%m-01')
),
TotalByMonth AS (
    SELECT
        month_start,
        COUNT(DISTINCT customer) AS total_customers
    FROM MonthlyInvoices
    GROUP BY month_start
),
MonthlyResults AS (
    SELECT
        ms.month_start AS `Month`,
        COALESCE(tb.total_customers, 0) AS `Total Customers`,
        COALESCE(rb.returning_customers, 0) AS `Returning Customers`,
        COALESCE(nb.new_customers, 0) AS `New Customers`,
        COALESCE(mb.multi_visit_customers, 0) AS `Multi-visit Customers`,
        ROUND(
            100.0 * COALESCE(rb.returning_customers, 0)
            / NULLIF(COALESCE(tb.total_customers, 0), 0),
            1
        ) AS `Returning %`,
        ROUND(
            100.0 * COALESCE(nb.new_customers, 0)
            / NULLIF(COALESCE(tb.total_customers, 0), 0),
            1
        ) AS `New %`,
        ROUND(
            100.0 * COALESCE(mb.multi_visit_customers, 0)
            / NULLIF(COALESCE(tb.total_customers, 0), 0),
            1
        ) AS `Multi-visit %`
    FROM MonthSeries ms
    LEFT JOIN TotalByMonth tb ON tb.month_start = ms.month_start
    LEFT JOIN ReturningByMonth rb ON rb.month_start = ms.month_start
    LEFT JOIN NewByMonth nb ON nb.month_start = ms.month_start
    LEFT JOIN MultiVisitByMonth mb ON mb.month_start = ms.month_start
)
SELECT
    `Month`,
    `Total Customers`,
    `Returning Customers`,
    `New Customers`,
    `Multi-visit Customers`,
    `Returning %`,
    `New %`,
    `Multi-visit %`
FROM MonthlyResults
ORDER BY `Month` ASC;"""

def build_cohort_retention_sql(max_months: int = MAX_COHORT_MONTHS) -> str:
    """Wide matrix in SQL — Metabase pivot cannot pass row/column dims on cell click."""
    months_union = " UNION ALL ".join(f"SELECT {m} AS months_since" for m in range(max_months + 1))
    pivot_cols = ",\n".join(
        f"    MAX(CASE WHEN cr.`Months Since First Visit` = {m} THEN cr.`Retention %` END) AS `{m}`"
        for m in range(max_months + 1)
    )
    return f"""
WITH FirstVisit AS (
    SELECT
        si.customer,
        DATE_FORMAT(MIN(si.posting_date), '%Y-%m-01') AS cohort_month,
        MIN(si.posting_date) AS first_visit_date
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
    GROUP BY si.customer
),
CohortSizes AS (
    SELECT cohort_month, COUNT(*) AS cohort_size
    FROM FirstVisit
    GROUP BY cohort_month
),
CustomerMonths AS (
    SELECT DISTINCT
        fv.customer,
        fv.cohort_month,
        DATE_FORMAT(si.posting_date, '%Y-%m-01') AS activity_month,
        TIMESTAMPDIFF(
            MONTH,
            fv.cohort_month,
            DATE_FORMAT(si.posting_date, '%Y-%m-01')
        ) AS months_since
    FROM `tabSales Invoice` si
    INNER JOIN FirstVisit fv ON fv.customer = si.customer
    WHERE {INVOICE_BASE}
      AND {CURRENT_MONTH_CUTOFF}
      AND DATE_FORMAT(si.posting_date, '%Y-%m-01') >= fv.cohort_month
),
RetentionCounts AS (
    SELECT
        cohort_month,
        months_since,
        COUNT(DISTINCT customer) AS retained
    FROM CustomerMonths
    WHERE months_since >= 0
    GROUP BY cohort_month, months_since
),
MonthOffsets AS (
    {months_union}
),
CohortMonthGrid AS (
    SELECT
        cs.cohort_month,
        cs.cohort_size,
        mo.months_since
    FROM CohortSizes cs
    CROSS JOIN MonthOffsets mo
    WHERE mo.months_since <= TIMESTAMPDIFF(
        MONTH,
        cs.cohort_month,
        DATE_FORMAT(CURDATE(), '%Y-%m-01')
    )
),
CohortResults AS (
    SELECT
        g.cohort_month AS `Cohort`,
        g.months_since AS `Months Since First Visit`,
        g.cohort_size AS `Cohort Size`,
        COALESCE(rc.retained, 0) AS `Returned`,
        ROUND(100.0 * COALESCE(rc.retained, 0) / g.cohort_size, 1) AS `Retention %`
    FROM CohortMonthGrid g
    LEFT JOIN RetentionCounts rc
        ON rc.cohort_month = g.cohort_month
       AND rc.months_since = g.months_since
)
SELECT
    cr.`Cohort`,
{pivot_cols}
FROM CohortResults cr
GROUP BY cr.`Cohort`
ORDER BY cr.`Cohort` ASC;"""


COHORT_RETENTION_SQL = build_cohort_retention_sql()

COHORT_CELL_CUSTOMERS_SQL = f"""
WITH FirstVisit AS (
    SELECT
        si.customer,
        DATE_FORMAT(MIN(si.posting_date), '%Y-%m-01') AS cohort_month,
        MIN(si.posting_date) AS first_visit_date
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
    GROUP BY si.customer
)
SELECT
    fv.customer AS `Customer ID`,
    COALESCE(c.customer_name, fv.customer) AS `Customer`,
    fv.first_visit_date AS `First Visit`,
    COUNT(DISTINCT si.name) AS `Invoices`,
    ROUND(SUM(si.base_grand_total), 0) AS `Revenue`,
    MIN(si.posting_date) AS `First Invoice This Period`,
    MAX(si.posting_date) AS `Last Invoice This Period`
FROM FirstVisit fv
INNER JOIN `tabCustomer` c ON c.name = fv.customer
INNER JOIN `tabSales Invoice` si ON si.customer = fv.customer
WHERE {INVOICE_BASE}
  AND (
    1 = 0
    [[OR (
      fv.cohort_month = {{{{cohort_month}}}}
      AND TIMESTAMPDIFF(
            MONTH,
            fv.cohort_month,
            DATE_FORMAT(si.posting_date, '%Y-%m-01')
          ) = {{{{months_since}}}}
    )]]
  )
GROUP BY fv.customer, c.customer_name, fv.first_visit_date
ORDER BY `Revenue` DESC, `Customer` ASC;"""

COHORT_ROSTER_SQL = f"""
WITH FirstVisit AS (
    SELECT
        si.customer,
        DATE_FORMAT(MIN(si.posting_date), '%Y-%m-01') AS cohort_month,
        MIN(si.posting_date) AS first_visit_date
    FROM `tabSales Invoice` si
    WHERE {INVOICE_BASE}
    GROUP BY si.customer
)
SELECT
    fv.customer AS `Customer ID`,
    COALESCE(c.customer_name, fv.customer) AS `Customer`,
    fv.first_visit_date AS `First Visit`,
    COUNT(DISTINCT si.name) AS `Lifetime Invoices`,
    ROUND(SUM(si.base_grand_total), 0) AS `Lifetime Revenue`,
    MAX(si.posting_date) AS `Last Visit`
FROM FirstVisit fv
INNER JOIN `tabCustomer` c ON c.name = fv.customer
INNER JOIN `tabSales Invoice` si ON si.customer = fv.customer
WHERE fv.cohort_month = {{{{cohort_month}}}}
  AND {INVOICE_BASE}
GROUP BY fv.customer, c.customer_name, fv.first_visit_date
ORDER BY `Lifetime Revenue` DESC, `Customer` ASC;"""

CHANNEL_TAG = {
    "id": CHANNEL_ID,
    "name": "channel",
    "display-name": "Channel",
    "type": "text",
    "default": "Both",
}

DATE_FILTER_TAG = {
    "id": DATE_FILTER_ID,
    "name": "date_filter",
    "display-name": "Month",
    "type": "dimension",
    "widget-type": "date/range",
    "dimension": ["field", {"lib/uuid": str(uuid.uuid4())}, 15000],
    "alias": "Month",
    "default": "past12months~",
}

COHORT_MONTH_TAG = {
    "id": COHORT_MONTH_PARAM_ID,
    "name": "cohort_month",
    "display-name": "Cohort Month",
    "type": "date",
    "required": False,
}

MONTHS_SINCE_TAG = {
    "id": MONTHS_SINCE_PARAM_ID,
    "name": "months_since",
    "display-name": "Months Since First Visit",
    "type": "number",
    "required": False,
}

COHORT_MONTH_PARAM = {
    "id": COHORT_MONTH_PARAM_ID,
    "type": "date/single",
    "target": ["variable", ["template-tag", "cohort_month"]],
    "name": "Cohort Month",
    "slug": "cohort_month_cell",
    "default": None,
}

MONTHS_SINCE_PARAM = {
    "id": MONTHS_SINCE_PARAM_ID,
    "type": "number/=",
    "target": ["variable", ["template-tag", "months_since"]],
    "name": "Months Since First Visit",
    "slug": "months_since",
    "default": None,
}

CUSTOMER_LINK = {
    "click_behavior": {
        "type": "link",
        "linkType": "url",
        "linkTemplate": "https://dashboard.masterpet.co.in/crm/customer/{{Customer ID}}",
    },
}

COHORT_MONTH_DASHBOARD_PARAM = {
    "id": COHORT_MONTH_PARAM_ID,
    "name": "Cohort Month",
    "slug": "cohort_month_cell",
    "type": "date/single",
    "sectionId": "date",
    "default": None,
}

MONTHS_SINCE_DASHBOARD_PARAM = {
    "id": MONTHS_SINCE_PARAM_ID,
    "name": "Months Since First Visit",
    "slug": "months_since",
    "type": "number/=",
    "sectionId": "number",
    "default": None,
}

MONTH_COLUMN_NAMES = [str(m) for m in range(MAX_COHORT_MONTHS + 1)]

COHORT_RETENTION_FORMATTING = [
    {
        "id": 1,
        "type": "single",
        "operator": ">=",
        "value": 50,
        "columns": MONTH_COLUMN_NAMES,
        "color": "#2D4A35",
        "highlight_row": False,
    },
    {
        "id": 2,
        "type": "single",
        "operator": ">=",
        "value": 25,
        "columns": MONTH_COLUMN_NAMES,
        "color": "#353D36",
        "highlight_row": False,
    },
]


def cohort_row_click_behavior() -> dict:
    return {
        "type": "link",
        "linkType": "question",
        "targetId": COHORT_ROSTER_CARD_ID,
        "parameterMapping": {
            COHORT_MONTH_PARAM_ID: {
                "source": {"type": "column", "id": "Cohort", "name": "Cohort"},
                "target": {"type": "parameter", "id": COHORT_MONTH_PARAM_ID},
                "id": COHORT_MONTH_PARAM_ID,
            },
        },
    }


def month_cell_click_behavior(month: int) -> dict:
    """Wide matrix passes Cohort from row; months_since is fixed per column."""
    return {
        "type": "link",
        "linkType": "url",
        "linkTemplate": (
            f"{QUESTION_DRILL_PATH}?cohort_month_cell={{{{Cohort}}}}&months_since={month}"
        ),
    }


def cohort_wide_column_settings() -> dict:
    settings = {
        '["name","Cohort"]': {
            "date_style": "MMMM YYYY",
            "click_behavior": cohort_row_click_behavior(),
        },
    }
    for month in range(MAX_COHORT_MONTHS + 1):
        col = str(month)
        settings[f'["name","{col}"]'] = {
            "decimals": 1,
            "suffix": "%",
            "click_behavior": month_cell_click_behavior(month),
        }
    return settings


COHORT_WIDE_COLUMN_SETTINGS = cohort_wide_column_settings()


def cohort_table_columns() -> list[dict]:
    return [{"name": "Cohort", "enabled": True}] + [
        {"name": str(m), "enabled": True} for m in range(MAX_COHORT_MONTHS + 1)
    ]


def cohort_dashcard_viz() -> dict:
    return {"column_settings": COHORT_WIDE_COLUMN_SETTINGS}


CHANNEL_PARAM = {
    "id": CHANNEL_ID,
    "type": "string/=",
    "target": ["variable", ["template-tag", "channel"]],
    "name": "Channel",
    "slug": "channel",
    "default": "Both",
    "values_query_type": "list",
    "values_source_type": "static-list",
    "values_source_config": {"values": [["Both"], ["Store"], ["Truck"]]},
}

DATE_PARAM = {
    "id": DATE_FILTER_ID,
    "type": "date/range",
    "target": ["dimension", ["template-tag", "date_filter"]],
    "name": "Month",
    "slug": "date",
    "default": "past12months~",
    "isMultiSelect": True,
}

CARDS = [
    {
        "id": 191,
        "name": "Returning Customers (MTD)",
        "description": (
            "Customers whose first paid invoice was before this month and who "
            "invoiced again month-to-date (excludes today and ₹0 invoices)."
        ),
        "display": "scalar",
        "sql": RETURNING_MTD_SQL,
        "tags": {"channel": CHANNEL_TAG},
        "params": [CHANNEL_PARAM],
        "viz": {"scalar.field": "Count", "scalar.compact_primary_number": True},
    },
    {
        "id": 192,
        "name": "Monthly Returning & Multi-visit Customers",
        "description": (
            "All months from inception. Active customers per month with new, returning, and "
            "multi-visit counts and % of total. Current month excludes today. "
            "New = first paid invoice in the month. Returning = first visit before the month. "
            "Multi-visit = 2+ paid invoices in the month. Excludes ₹0 invoices."
        ),
        "display": "combo",
        "sql": MONTHLY_RETENTION_TREND_SQL,
        "tags": {"channel": CHANNEL_TAG},
        "params": [CHANNEL_PARAM],
        "viz": {
            "graph.dimensions": ["Month"],
            "graph.metrics": [
                "Total Customers",
                "Returning Customers",
                "New Customers",
                "Multi-visit Customers",
                "Returning %",
                "New %",
                "Multi-visit %",
            ],
            "graph.x_axis.scale": "timeseries",
            "graph.x_axis.title_text": "Month",
            "graph.y_axis.title_text": "Customers",
            "graph.y_axis.auto_split": True,
            "graph.show_values": False,
            "series_settings": {
                "Total Customers": {
                    "color": "#7172AD",
                    "title": "Total customers",
                    "display": "bar",
                    "axis": "left",
                },
                "Returning Customers": {
                    "color": "#509EE3",
                    "title": "Returning customers",
                    "display": "bar",
                    "axis": "left",
                },
                "New Customers": {
                    "color": "#F9D45C",
                    "title": "New customers",
                    "display": "bar",
                    "axis": "left",
                },
                "Multi-visit Customers": {
                    "color": "#A989C5",
                    "title": "Multi-visit customers",
                    "display": "bar",
                    "axis": "left",
                },
                "Returning %": {
                    "color": "#88BF4D",
                    "title": "Returning %",
                    "display": "line",
                    "line.style": "solid",
                    "axis": "right",
                },
                "New %": {
                    "color": "#F2A86F",
                    "title": "New %",
                    "display": "line",
                    "line.style": "solid",
                    "axis": "right",
                },
                "Multi-visit %": {
                    "color": "#EF8C8C",
                    "title": "Multi-visit %",
                    "display": "line",
                    "line.style": "solid",
                    "axis": "right",
                },
            },
            "column_settings": {
                '["name","Returning %"]': {"decimals": 1, "suffix": "%"},
                '["name","New %"]': {"decimals": 1, "suffix": "%"},
                '["name","Multi-visit %"]': {"decimals": 1, "suffix": "%"},
            },
        },
    },
    {
        "id": 193,
        "name": "Customer Cohort Retention",
        "description": (
            "Cohort matrix from inception — rows = acquisition month, columns = months "
            "since first visit. Click a % cell for customers active that month; click a "
            "cohort name for the full roster. Excludes ₹0 invoices."
        ),
        "display": "table",
        "sql": COHORT_RETENTION_SQL,
        "tags": {"channel": CHANNEL_TAG},
        "params": [CHANNEL_PARAM],
        "viz": {
            "table.columns": cohort_table_columns(),
            "table.column_formatting": COHORT_RETENTION_FORMATTING,
            "column_settings": COHORT_WIDE_COLUMN_SETTINGS,
        },
    },
    {
        "id": DETAIL_CARD_ID,
        "name": "Cohort Retention — Customers",
        "description": (
            "Customers from the selected cohort who invoiced in the chosen "
            "months-since-first-visit period."
        ),
        "display": "table",
        "sql": COHORT_CELL_CUSTOMERS_SQL,
        "tags": {
            "channel": CHANNEL_TAG,
            "cohort_month": COHORT_MONTH_TAG,
            "months_since": MONTHS_SINCE_TAG,
        },
        "params": [COHORT_MONTH_PARAM, MONTHS_SINCE_PARAM, CHANNEL_PARAM],
        "viz": {
            "table.columns": [
                {"name": "Customer", "enabled": True},
                {"name": "First Visit", "enabled": True},
                {"name": "Invoices", "enabled": True},
                {"name": "Revenue", "enabled": True},
                {"name": "First Invoice This Period", "enabled": True},
                {"name": "Last Invoice This Period", "enabled": True},
                {"name": "Customer ID", "enabled": False},
            ],
            "column_settings": {
                '["name","Customer"]': CUSTOMER_LINK,
                '["name","First Visit"]': {"date_style": "MMM D, YYYY"},
                '["name","Revenue"]': {"prefix": "₹", "number_separators": ","},
                '["name","First Invoice This Period"]': {"date_style": "MMM D, YYYY"},
                '["name","Last Invoice This Period"]': {"date_style": "MMM D, YYYY"},
            },
        },
    },
    {
        "id": COHORT_ROSTER_CARD_ID,
        "name": "Cohort Retention — Cohort Roster",
        "description": "All customers acquired in the selected cohort month.",
        "display": "table",
        "sql": COHORT_ROSTER_SQL,
        "tags": {"channel": CHANNEL_TAG, "cohort_month": COHORT_MONTH_TAG},
        "params": [COHORT_MONTH_PARAM, CHANNEL_PARAM],
        "viz": {
            "table.columns": [
                {"name": "Customer", "enabled": True},
                {"name": "First Visit", "enabled": True},
                {"name": "Lifetime Invoices", "enabled": True},
                {"name": "Lifetime Revenue", "enabled": True},
                {"name": "Last Visit", "enabled": True},
                {"name": "Customer ID", "enabled": False},
            ],
            "column_settings": {
                '["name","Customer"]': CUSTOMER_LINK,
                '["name","First Visit"]': {"date_style": "MMM D, YYYY"},
                '["name","Lifetime Revenue"]': {"prefix": "₹", "number_separators": ","},
                '["name","Last Visit"]': {"date_style": "MMM D, YYYY"},
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


def channel_mappings(card_id: int) -> list[dict]:
    return [
        {
            "parameter_id": CHANNEL_ID,
            "card_id": card_id,
            "target": ["variable", ["template-tag", "channel"]],
        }
    ]


def trend_mappings(card_id: int) -> list[dict]:
    return [
        {
            "parameter_id": DATE_FILTER_ID,
            "card_id": card_id,
            "target": ["dimension", ["template-tag", "date_filter"]],
        },
        *channel_mappings(card_id),
    ]


def cohort_detail_mappings(card_id: int) -> list[dict]:
    return [
        {
            "parameter_id": COHORT_MONTH_PARAM_ID,
            "card_id": card_id,
            "target": ["variable", ["template-tag", "cohort_month"]],
        },
        {
            "parameter_id": MONTHS_SINCE_PARAM_ID,
            "card_id": card_id,
            "target": ["variable", ["template-tag", "months_since"]],
        },
        *channel_mappings(card_id),
    ]


def remove_cohort_drill_dashboard_params() -> None:
    current = psql(f"SELECT parameters FROM report_dashboard WHERE id = {DASHBOARD_ID};")
    params = json.loads(current) if current else []
    drill_ids = {COHORT_DATE_FILTER_ID, COHORT_MONTH_PARAM_ID, MONTHS_SINCE_PARAM_ID}
    params = [p for p in params if p.get("id") not in drill_ids]
    psql(
        f"UPDATE report_dashboard SET parameters = '{esc(json.dumps(params))}', updated_at = NOW() "
        f"WHERE id = {DASHBOARD_ID};"
    )


def remove_cohort_dashboard_param() -> None:
    remove_cohort_drill_dashboard_params()


def upsert_card(spec: dict) -> None:
    card_id = spec["id"]
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
    existing = psql(f"SELECT id FROM report_card WHERE id = {card_id} LIMIT 1;")
    if existing:
        psql(
            f"UPDATE report_card SET name = '{esc(spec['name'])}', "
            f"description = '{esc(spec['description'])}', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(spec['params']))}', "
            f"visualization_settings = '{esc(json.dumps(spec['viz']))}', "
            f"display = '{esc(spec['display'])}', cache_invalidated_at = NOW(), updated_at = NOW() "
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
    '{esc(spec['display'])}', '{esc(json.dumps(dq))}', '{esc(json.dumps(spec['viz']))}',
    {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(spec['params']))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
);
"""
    )
    print(f"Created card {card_id}: {spec['name']}")


def main() -> None:
    for spec in CARDS:
        upsert_card(spec)
    remove_cohort_dashboard_param()

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
