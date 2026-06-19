#!/usr/bin/env python3
"""Add follow-up KPI scalar cards to dashboard 37."""

from __future__ import annotations

import json
import secrets
import subprocess
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "update_followup_table",
    Path(__file__).with_name("update-followup-table.py"),
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)

CHANNEL_ID = _mod.CHANNEL_ID
COHORT_ID = _mod.COHORT_ID
DASHBOARD_ID = _mod.DASHBOARD_ID
FOOD_ITEM_GROUPS = _mod.FOOD_ITEM_GROUPS
PROFILE_ID = _mod.PROFILE_ID
SHOW_CLOSED_WON_ID = _mod.SHOW_CLOSED_WON_ID

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
CARD_SCHEMA = 23
FOLLOWUP_TABLE_CARD_ID = 164
KPI_ROW_1 = 78
KPI_ROW_2 = 82
TABLE_ROW = 93
KPI_SIZE_X = 6
KPI_SIZE_Y = 4

KPI_PARAMETERS = ["channel", "purchase_profile", "cohort", "show_closed_won"]

TEMPLATE_TAGS = {
    "channel": {
        "id": CHANNEL_ID,
        "name": "channel",
        "display-name": "Channel",
        "type": "text",
        "default": "Both",
    },
    "purchase_profile": {
        "id": PROFILE_ID,
        "name": "purchase_profile",
        "display-name": "Purchase Profile",
        "type": "text",
        "default": "All",
    },
    "cohort": {
        "id": COHORT_ID,
        "name": "cohort",
        "display-name": "Cohort",
        "type": "text",
        "default": "All",
    },
    "show_closed_won": {
        "id": SHOW_CLOSED_WON_ID,
        "name": "show_closed_won",
        "display-name": "Show Closed Won",
        "type": "text",
        "default": "Hide",
    },
}

CARD_PARAMETERS = [
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
    {
        "id": PROFILE_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "purchase_profile"]],
        "name": "Purchase Profile",
        "slug": "purchase_profile",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Grooming only"],
                ["Products only"],
                ["Grooming & Products"],
            ]
        },
    },
    {
        "id": COHORT_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "cohort"]],
        "name": "Cohort",
        "slug": "cohort",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Dormant (45-90 days)"],
                ["Lost (90+ days)"],
                ["Dormant + Lost"],
            ]
        },
    },
    {
        "id": SHOW_CLOSED_WON_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "show_closed_won"]],
        "name": "Show Closed Won",
        "slug": "show_closed_won",
        "default": "Hide",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [["Hide"], ["Show"]]},
    },
]

PARAMETER_MAPPINGS = [
    {"parameter_id": CHANNEL_ID, "target": ["variable", ["template-tag", "channel"]]},
    {"parameter_id": PROFILE_ID, "target": ["variable", ["template-tag", "purchase_profile"]]},
    {"parameter_id": COHORT_ID, "target": ["variable", ["template-tag", "cohort"]]},
    {"parameter_id": SHOW_CLOSED_WON_ID, "target": ["variable", ["template-tag", "show_closed_won"]]},
]

BASE_CTES = (
    """WITH CustomerChannels AS (
    SELECT
        si.customer,
        MAX(CASE WHEN si.pos_profile = 'Vennala POS' THEN 1 ELSE 0 END) AS has_store,
        MAX(
            CASE
                WHEN IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '' THEN 1 ELSE 0
            END
        ) AS has_truck
    FROM `tabSales Invoice` si
    WHERE si.docstatus = 1
    GROUP BY si.customer
),
WorkQueueCustomers AS (
    SELECT DISTINCT customer
    FROM `tabCustomer Follow Ups`
    UNION
    SELECT DISTINCT si.customer
    FROM `tabSales Invoice` si
    JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
    WHERE si.docstatus = 1
      AND sii.item_group IN ('Grooming Services', 'Travel Services')
      AND si.posting_date < DATE_SUB(CURDATE(), INTERVAL 45 DAY)
),
ChannelInvoices AS (
    SELECT
        si.customer,
        si.name AS invoice_name,
        si.posting_date,
        si.pos_profile,
        si.base_grand_total
    FROM `tabSales Invoice` si
    JOIN WorkQueueCustomers wq ON si.customer = wq.customer
    WHERE si.docstatus = 1
      AND (
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
        OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
      )
),
InvoiceRollup AS (
    SELECT
        ci.customer,
        ci.invoice_name,
        MAX(ci.posting_date) AS posting_date,
        MAX(ci.base_grand_total) AS base_grand_total,
        SUM(sii.base_amount) AS invoice_base_sum,
        SUM(
            CASE
                WHEN sii.item_group IN ('Grooming Services', 'Travel Services')
                THEN sii.base_amount ELSE 0
            END
        ) AS grooming_base_sum,
        MAX(
            CASE
                WHEN sii.item_group IN ('Grooming Services', 'Travel Services')
                THEN 1 ELSE 0
            END
        ) AS has_grooming,
        MAX(
            CASE
                WHEN sii.item_group NOT IN ('Grooming Services', 'Travel Services')
                THEN 1 ELSE 0
            END
        ) AS has_products
    FROM ChannelInvoices ci
    JOIN `tabSales Invoice Item` sii ON ci.invoice_name = sii.parent
    GROUP BY ci.customer, ci.invoice_name
),
CustomerPurchases AS (
    SELECT
        customer,
        MAX(has_grooming) AS has_grooming,
        MAX(has_products) AS has_products
    FROM InvoiceRollup
    GROUP BY customer
),
GroomingStats AS (
    SELECT
        customer,
        MAX(posting_date) AS last_grooming_visit
    FROM InvoiceRollup
    WHERE grooming_base_sum > 0
    GROUP BY customer
),
AllChannelGrooming AS (
    SELECT
        wq.customer,
        MAX(si.posting_date) AS last_grooming_visit
    FROM WorkQueueCustomers wq
    JOIN `tabSales Invoice` si ON si.customer = wq.customer AND si.docstatus = 1
    JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
    WHERE sii.item_group IN ('Grooming Services', 'Travel Services')
    GROUP BY wq.customer
),
AllChannelFood AS (
    SELECT
        wq.customer,
        MAX(si.posting_date) AS last_food_date
    FROM WorkQueueCustomers wq
    JOIN `tabSales Invoice` si ON si.customer = wq.customer AND si.docstatus = 1
    JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
    WHERE sii.item_group IN ("""
    + FOOD_ITEM_GROUPS
    + """)
    GROUP BY wq.customer
),
LatestFollowUp AS (
    SELECT
        cfu.customer,
        cfu.creation AS last_followup_date,
        cfu.next_follow_up,
        cfu.no_follow_up_reason,
        ROW_NUMBER() OVER (PARTITION BY cfu.customer ORDER BY cfu.creation DESC) AS rn
    FROM `tabCustomer Follow Ups` cfu
    JOIN WorkQueueCustomers wq ON cfu.customer = wq.customer
),
FollowUpQueue AS (
    SELECT
        CASE
            WHEN lfu.last_followup_date IS NULL THEN 'Never Called'
            WHEN lfu.no_follow_up_reason IS NOT NULL AND lfu.no_follow_up_reason != '' THEN 'Do Not Call'
            WHEN gs.last_grooming_visit IS NOT NULL
                 AND lfu.next_follow_up IS NOT NULL
                 AND gs.last_grooming_visit >= lfu.next_follow_up
                 AND gs.last_grooming_visit >= DATE(lfu.last_followup_date)
                THEN 'Closed Won'
            WHEN DATEDIFF(lfu.next_follow_up, CURDATE()) < 0 THEN 'Overdue'
            WHEN DATEDIFF(lfu.next_follow_up, CURDATE()) = 0 THEN 'Due Today'
            WHEN lfu.next_follow_up IS NOT NULL THEN 'Scheduled'
            ELSE 'Needs Review'
        END AS call_priority,
        CASE
            WHEN acf.last_food_date IS NOT NULL
                 AND DATEDIFF(CURDATE(), acf.last_food_date) >= 30
                THEN 'Resupply due'
            WHEN acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL
                THEN 'Cross-sell food'
            ELSE 'None'
        END AS food_opportunity
    FROM WorkQueueCustomers wq
    JOIN CustomerChannels ch ON wq.customer = ch.customer
    LEFT JOIN CustomerPurchases cp ON wq.customer = cp.customer
    LEFT JOIN GroomingStats gs ON wq.customer = gs.customer
    LEFT JOIN AllChannelGrooming acg ON wq.customer = acg.customer
    LEFT JOIN AllChannelFood acf ON wq.customer = acf.customer
    LEFT JOIN LatestFollowUp lfu ON wq.customer = lfu.customer AND lfu.rn = 1
    WHERE (
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
      )
      AND (
        {{show_closed_won}} = 'Show'
        OR NOT (
            gs.last_grooming_visit IS NOT NULL
            AND lfu.next_follow_up IS NOT NULL
            AND gs.last_grooming_visit >= lfu.next_follow_up
            AND gs.last_grooming_visit >= DATE(lfu.last_followup_date)
        )
      )
)"""
)

# name, description, legacy names, filter, row, col
METRICS = [
    (
        "Due today",
        "Follow-up date is today — call now",
        ["Calls Due Today", "Calls Today", "Follow-ups: Calls Today"],
        "call_priority = 'Due Today'",
        KPI_ROW_1,
        0,
    ),
    (
        "Past due",
        "Follow-up date passed — call ASAP",
        ["Overdue", "Follow-ups: Overdue"],
        "call_priority = 'Overdue'",
        KPI_ROW_1,
        6,
    ),
    (
        "Never called",
        "In queue but no call logged yet",
        ["Never Called", "Follow-ups: Never Called"],
        "call_priority = 'Never Called'",
        KPI_ROW_1,
        12,
    ),
    (
        "Future calls",
        "Follow-up scheduled for a later date",
        ["Scheduled", "Follow-ups: Scheduled"],
        "call_priority = 'Scheduled'",
        KPI_ROW_1,
        18,
    ),
    (
        "Missing date",
        "Called before but no next follow-up set",
        ["Needs Review", "Follow-ups: Needs Review"],
        "call_priority = 'Needs Review'",
        KPI_ROW_2,
        0,
    ),
    (
        "Food cross-sell",
        "Grooms with us but never bought food",
        ["Cross-sell Food", "Follow-ups: Cross-sell Food"],
        "food_opportunity = 'Cross-sell food'",
        KPI_ROW_2,
        6,
    ),
    (
        "Food resupply",
        "Bought food; last purchase 30+ days ago",
        ["Resupply Due", "Follow-ups: Resupply Due"],
        "food_opportunity = 'Resupply due'",
        KPI_ROW_2,
        12,
    ),
    (
        "Total in queue",
        "All customers matching current filters",
        ["In Queue", "Follow-ups: In Queue"],
        "1 = 1",
        KPI_ROW_2,
        18,
    ),
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


def kpi_sql(where_clause: str) -> str:
    return (
        BASE_CTES
        + f"""
SELECT COUNT(*) AS `Count`
FROM FollowUpQueue
WHERE {where_clause};"""
    )


def legacy_names_sql(names: list[str]) -> str:
    return ", ".join(f"'{esc(name)}'" for name in names)


def upsert_card(name: str, description: str, legacy_names: list[str], sql: str) -> int:
    all_names = [name, *legacy_names]
    existing = psql(
        f"SELECT id FROM report_card WHERE name IN ({legacy_names_sql(all_names)}) "
        f"AND archived = false ORDER BY id LIMIT 1;"
    )
    dq = {
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
    viz = {
        "scalar.field": "Count",
        "scalar.switch_positive_negative": False,
        "scalar.compact_primary_number": True,
    }
    if existing:
        card_id = int(existing)
        psql(
            f"UPDATE report_card SET name = '{esc(name)}', description = '{esc(description)}', "
            f"dataset_query = '{esc(json.dumps(dq))}', "
            f"parameters = '{esc(json.dumps(CARD_PARAMETERS))}', "
            f"visualization_settings = '{esc(json.dumps(viz))}', display = 'scalar', updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}: {name}")
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
    '{now}', '{now}', '{esc(name)}', '{esc(description)}', 'scalar', '{esc(json.dumps(dq))}',
    '{esc(json.dumps(viz))}', {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
)
RETURNING id;
"""
        )
    )
    print(f"Created card {card_id}: {name}")
    return card_id


def place_on_dashboard(card_id: int, row: int, col: int) -> None:
    mappings = [{**m, "card_id": card_id} for m in PARAMETER_MAPPINGS]
    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    if existing:
        psql(
            f"UPDATE report_dashboardcard SET row = {row}, col = {col}, "
            f"size_x = {KPI_SIZE_X}, size_y = {KPI_SIZE_Y}, "
            f"parameter_mappings = '{esc(json.dumps(mappings))}', updated_at = NOW() "
            f"WHERE id = {existing};"
        )
        return

    psql(
        f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id
) VALUES (
    {KPI_SIZE_X}, {KPI_SIZE_Y}, {row}, {col}, {card_id}, {DASHBOARD_ID},
    '{esc(json.dumps(mappings))}', '{{}}', '{entity_id()}'
);
"""
    )


def main() -> None:
    for name, description, legacy_names, where_clause, _row, _col in METRICS:
        upsert_card(name, description, legacy_names, kpi_sql(where_clause))

    _spec = importlib.util.spec_from_file_location(
        "reorganize_dashboard_37",
        Path(__file__).with_name("reorganize-dashboard-37.py"),
    )
    mod = importlib.util.module_from_spec(_spec)
    assert _spec.loader is not None
    _spec.loader.exec_module(mod)
    mod.main()
    print(f"Done: dashboard {DASHBOARD_ID} follow-up KPI cards")


if __name__ == "__main__":
    main()
