#!/usr/bin/env python3
"""Create customer follow-up table with channel and purchase profile filters."""

from __future__ import annotations

import json
import secrets
import subprocess
import uuid
from datetime import datetime, timezone

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import agent_case_sql

DATABASE_ID = 2
COLLECTION_ID = 16
CREATOR_ID = 1
DASHBOARD_ID = 37
CARD_SCHEMA = 23
CARD_NAME = "Customer Follow-ups & Purchase Profile"

DATE_RANGE_ID = str(uuid.uuid4())
CHANNEL_ID = str(uuid.uuid4())
PROFILE_ID = str(uuid.uuid4())
DASH_DATE_ID = "d386a5dd-02d4-43aa-a5d1-20e0493590e9"  # existing sales chart date
FOLLOWUP_DATE_ID = "a8f3c2e1-4b5d-6a7f-8e9d-0c1b2a3d4e5f"
FOLLOWUP_CHANNEL_ID = "b9e4d3c2-5a6f-7b8c-9d0e-1f2a3b4c5d6e"
FOLLOWUP_PROFILE_ID = "c0f5e4d3-6a7b-8c9d-0e1f-2a3b4c5d6e7f"

AGENT_NAME_CASE = agent_case_sql("agent", else_expr="ELSE agent")

SQL = """WITH CustomerPurchases AS (
    SELECT
        si.customer,
        MAX(
            CASE
                WHEN sii.item_group LIKE '%Grooming%' OR sii.item_group LIKE '%Service%'
                THEN 1 ELSE 0
            END
        ) AS has_grooming,
        MAX(
            CASE
                WHEN sii.item_group NOT LIKE '%Grooming%' AND sii.item_group NOT LIKE '%Service%'
                THEN 1 ELSE 0
            END
        ) AS has_products
    FROM `tabSales Invoice` si
    JOIN `tabSales Invoice Item` sii ON si.name = sii.parent
    WHERE si.docstatus = 1
      [[AND {{date_range}}]]
      AND (
        {{channel}} = 'Both'
        OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
        OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
      )
    GROUP BY si.customer
),
LatestFollowUp AS (
    SELECT
        customer,
        creation AS last_followup_date,
        call_outcome,
        quick_note,
        """ + AGENT_NAME_CASE + """ AS agent_name,
        ROW_NUMBER() OVER (PARTITION BY customer ORDER BY creation DESC) AS row_num
    FROM `tabCustomer Follow Ups`
)
SELECT
    c.name AS `Customer ID`,
    c.customer_name AS `Customer Name`,
    NULLIF(
        TRIM(BOTH ', ' FROM CONCAT(
            IF(COALESCE(cp.has_grooming, 0) = 1, 'Grooming', ''),
            IF(COALESCE(cp.has_grooming, 0) = 1 AND COALESCE(cp.has_products, 0) = 1, ', ', ''),
            IF(COALESCE(cp.has_products, 0) = 1, 'Products', '')
        )),
        ''
    ) AS `Purchase Profile`,
    GROUP_CONCAT(
        DISTINCT CASE
            WHEN si.pos_profile = 'Vennala POS' THEN 'Store'
            ELSE 'Truck'
        END
        ORDER BY 1 SEPARATOR ', '
    ) AS `Channels`,
    MAX(si.posting_date) AS `Last Visit`,
    DATEDIFF(CURDATE(), MAX(si.posting_date)) AS `Days Since Last Visit`,
    COUNT(si.name) AS `Total Invoices`,
    ROUND(SUM(si.base_grand_total), 0) AS `Total Spent (incl. tax)`,
    lfu.last_followup_date AS `Last Follow-up Date`,
    lfu.agent_name AS `Agent`,
    lfu.call_outcome AS `Last Outcome`,
    lfu.quick_note AS `Last Note`
FROM `tabSales Invoice` si
JOIN `tabCustomer` c ON si.customer = c.name
LEFT JOIN CustomerPurchases cp ON c.name = cp.customer
LEFT JOIN LatestFollowUp lfu ON c.name = lfu.customer AND lfu.row_num = 1
WHERE si.docstatus = 1
  [[AND {{date_range}}]]
  AND (
    {{channel}} = 'Both'
    OR ({{channel}} = 'Store' AND si.pos_profile = 'Vennala POS')
    OR ({{channel}} = 'Truck' AND IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '')
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
GROUP BY
    c.name,
    c.customer_name,
    cp.has_grooming,
    cp.has_products,
    lfu.last_followup_date,
    lfu.agent_name,
    lfu.call_outcome,
    lfu.quick_note
ORDER BY `Days Since Last Visit` DESC;"""

TEMPLATE_TAGS = {
    "date_range": {
        "id": DATE_RANGE_ID,
        "name": "date_range",
        "display-name": "Invoice Date Range",
        "type": "dimension",
        "widget-type": "date/range",
        "dimension": ["field", {"lib/uuid": str(uuid.uuid4())}, 15000],
        "alias": "si.posting_date",
        "default": None,
    },
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
}

CARD_PARAMETERS = [
    {
        "id": DATE_RANGE_ID,
        "type": "date/range",
        "target": ["dimension", ["template-tag", "date_range"]],
        "name": "Invoice Date Range",
        "slug": "date_range",
        "default": None,
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
        "values_source_config": {
            "values": [["Both"], ["Store"], ["Truck"]],
        },
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
            ],
        },
    },
]

VIZ = {
    "table.columns": [
        {"name": "Customer ID", "enabled": True},
        {"name": "Customer Name", "enabled": True},
        {"name": "Purchase Profile", "enabled": True},
        {"name": "Channels", "enabled": True},
        {"name": "Last Visit", "enabled": True},
        {"name": "Days Since Last Visit", "enabled": True},
        {"name": "Total Invoices", "enabled": True},
        {"name": "Total Spent (incl. tax)", "enabled": True},
        {"name": "Last Follow-up Date", "enabled": True},
        {"name": "Agent", "enabled": True},
        {"name": "Last Outcome", "enabled": True},
        {"name": "Last Note", "enabled": True},
    ],
    "column_settings": {
        '["name","Total Spent (incl. tax)"]': {
            "number_style": "currency",
            "currency": "INR",
            "currency_style": "symbol",
            "decimals": 0,
        },
        '["name","Days Since Last Visit"]': {"decimals": 0},
    },
}


def entity_id() -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(secrets.choice(alphabet) for _ in range(21))


def psql(sql: str) -> str:
    r = subprocess.run(
        ["docker", "exec", "metabase-postgres", "psql", "-U", "metabase", "-d", "metabase", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    return lines[0] if lines else ""


def esc(s: str) -> str:
    return s.replace("'", "''")


def main() -> None:
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
            f"visualization_settings = '{esc(json.dumps(VIZ))}', display = 'table', updated_at = NOW() "
            f"WHERE id = {card_id};"
        )
        print(f"Updated card {card_id}")
    else:
        now = datetime.now(timezone.utc).isoformat()
        card_id = int(
            psql(
                f"""
INSERT INTO report_card (
    created_at, updated_at, name, display, dataset_query, visualization_settings,
    creator_id, database_id, query_type, collection_id, parameters, card_schema, type,
    entity_id, last_used_at
) VALUES (
    '{now}', '{now}', '{esc(CARD_NAME)}', 'table', '{esc(json.dumps(dq))}',
    '{esc(json.dumps(VIZ))}', {CREATOR_ID}, {DATABASE_ID}, 'native', {COLLECTION_ID},
    '{esc(json.dumps(CARD_PARAMETERS))}', {CARD_SCHEMA}, 'question',
    '{entity_id()}', '{now}'
)
RETURNING id;
"""
            )
        )
        print(f"Created card {card_id}")

    placement = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {card_id} LIMIT 1;"
    )
    parameter_mappings = json.dumps(
        [
            {
                "parameter_id": FOLLOWUP_DATE_ID,
                "card_id": card_id,
                "target": ["dimension", ["template-tag", "date_range"], {"stage-number": 0}],
            },
            {
                "parameter_id": FOLLOWUP_CHANNEL_ID,
                "card_id": card_id,
                "target": ["variable", ["template-tag", "channel"]],
            },
            {
                "parameter_id": FOLLOWUP_PROFILE_ID,
                "card_id": card_id,
                "target": ["variable", ["template-tag", "purchase_profile"]],
            },
        ]
    )
    inline_params = esc(
        json.dumps([FOLLOWUP_DATE_ID, FOLLOWUP_CHANNEL_ID, FOLLOWUP_PROFILE_ID])
    )
    if placement:
        psql(
            f"UPDATE report_dashboardcard SET parameter_mappings = '{esc(parameter_mappings)}', "
            f"inline_parameters = '{inline_params}', updated_at = NOW() WHERE id = {placement};"
        )
    else:
        psql(
            f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id,
    parameter_mappings, visualization_settings, entity_id, inline_parameters
) VALUES (24, 10, 47, 0, {card_id}, {DASHBOARD_ID}, '{esc(parameter_mappings)}', '{{}}', '{entity_id()}', '{inline_params}');
"""
        )
        print(f"Added card {card_id} to dashboard {DASHBOARD_ID}")

    dashboard_params = [
        {
            "name": "Date",
            "slug": "date",
            "id": DASH_DATE_ID,
            "type": "date/range",
            "sectionId": "date",
            "default": "past3months~",
        },
        {
            "name": "Invoice Date",
            "slug": "followup_date",
            "id": FOLLOWUP_DATE_ID,
            "type": "date/range",
            "sectionId": "date",
            "default": None,
        },
        {
            "name": "Channel",
            "slug": "followup_channel",
            "id": FOLLOWUP_CHANNEL_ID,
            "type": "string/=",
            "sectionId": "string",
            "default": "Both",
            "values_query_type": "list",
            "values_source_type": "static-list",
            "values_source_config": {"values": [["Both"], ["Store"], ["Truck"]]},
        },
        {
            "name": "Purchase Profile",
            "slug": "followup_profile",
            "id": FOLLOWUP_PROFILE_ID,
            "type": "string/=",
            "sectionId": "string",
            "default": "All",
            "values_query_type": "list",
            "values_source_type": "static-list",
            "values_source_config": {
                "values": [["All"], ["Grooming only"], ["Products only"], ["Grooming & Products"]]
            },
        },
    ]
    psql(
        f"UPDATE report_dashboard SET parameters = '{esc(json.dumps(dashboard_params))}', updated_at = NOW() "
        f"WHERE id = {DASHBOARD_ID};"
    )
    print("Follow-up filters pinned inline on card 164")


if __name__ == "__main__":
    main()
