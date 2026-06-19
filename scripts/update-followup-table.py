#!/usr/bin/env python3
"""Update customer follow-up table: cohort filter, priority labels, no invoice date."""

from __future__ import annotations

import json
import subprocess
import uuid

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agents_config import agent_case_sql

CARD_ID = 164
DASHBOARD_ID = 37
DASH_DATE_ID = "d386a5dd-02d4-43aa-a5d1-20e0493590e9"
CHANNEL_ID = "b9e4d3c2-5a6f-7b8c-9d0e-1f2a3b4c5d6e"
PROFILE_ID = "c0f5e4d3-6a7b-8c9d-0e1f-2a3b4c5d6e7f"
COHORT_ID = "d1a6f5e4-7b8c-9d0e-1f2a-3b4c5d6e7f8a"
SHOW_CLOSED_WON_ID = "e2b7a6f5-8c9d-0e1f-2a3b-4c5d6e7f8a9b"
CROSS_SELL_SEGMENT_ID = "f3a8b7c6-9d0e-1f2a-3b4c-5d6e7f8a9b0c"
FOOD_OPPORTUNITY_ID = "a4b9c8d7-0e1f-2a3b-4c5d-6e7f8a9b0c1d"
FOOD_BUYER_ID = "b5c0d9e8-1f2a-3b4c-5d6e-7f8a9b0c1d2e"
AGENT_DATE_ID = "aff7e035-cf93-4608-9bc8-b996aed9a66e"

FOOD_ITEM_GROUPS = (
    "'Dogs – Dry Food', 'Dogs – Wet Food', 'Dogs – Treats', "
    "'Cats – Dry Food', 'Cats – Wet Food', 'Cats – Treats', "
    "'Cats – Creamy Treats', 'Pet Food'"
)

FOOD_TYPE_CASE = """CASE
                WHEN sii.item_group = 'Dogs – Dry Food' THEN 'Dog Dry'
                WHEN sii.item_group = 'Dogs – Wet Food' THEN 'Dog Wet'
                WHEN sii.item_group = 'Dogs – Treats' THEN 'Dog Treats'
                WHEN sii.item_group = 'Cats – Dry Food' THEN 'Cat Dry'
                WHEN sii.item_group = 'Cats – Wet Food' THEN 'Cat Wet'
                WHEN sii.item_group = 'Cats – Treats' THEN 'Cat Treats'
                WHEN sii.item_group = 'Cats – Creamy Treats' THEN 'Cat Creamy Treats'
                WHEN sii.item_group = 'Pet Food' THEN 'Pet Food'
            END"""

AGENT_NAME_CASE = agent_case_sql("cfu.agent", else_expr="ELSE cfu.agent")

SQL = ("""WITH CustomerChannels AS (
    SELECT
        si.customer,
        MAX(CASE WHEN si.pos_profile = 'Vennala POS' THEN 1 ELSE 0 END) AS has_store,
        MAX(
            CASE
                WHEN IFNULL(NULLIF(TRIM(si.pos_profile), ''), '') = '' THEN 1 ELSE 0
            END
        ) AS has_truck,
        GROUP_CONCAT(
            DISTINCT CASE
                WHEN si.pos_profile = 'Vennala POS' THEN 'Store'
                ELSE 'Truck'
            END
            ORDER BY 1 SEPARATOR ', '
        ) AS all_channels
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
        MAX(ci.pos_profile) AS pos_profile,
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
CustomerStats AS (
    SELECT
        customer,
        MAX(posting_date) AS last_visit_date,
        COUNT(*) AS total_invoices,
        SUM(base_grand_total) AS total_spent
    FROM InvoiceRollup
    GROUP BY customer
),
GroomingStats AS (
    SELECT
        customer,
        MAX(posting_date) AS last_grooming_visit,
        COUNT(*) AS grooming_invoices,
        SUM(
            base_grand_total * grooming_base_sum / NULLIF(invoice_base_sum, 0)
        ) AS grooming_spent
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
        MAX(si.posting_date) AS last_food_date,
        NULLIF(
            TRIM(BOTH ', ' FROM GROUP_CONCAT(
                DISTINCT """
    + FOOD_TYPE_CASE
    + """
                ORDER BY 1 SEPARATOR ', '
            )),
            ''
        ) AS food_types
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
        cfu.call_outcome,
        cfu.quick_note,
        cfu.next_follow_up,
        cfu.no_follow_up_reason,
        cfu.no_follow_up_details,
        """ + AGENT_NAME_CASE + """ AS agent_name,
        ROW_NUMBER() OVER (PARTITION BY cfu.customer ORDER BY cfu.creation DESC) AS rn
    FROM `tabCustomer Follow Ups` cfu
    JOIN WorkQueueCustomers wq ON cfu.customer = wq.customer
)
SELECT
    cs.customer AS `Customer ID`,
    c.customer_name AS `Customer`,
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
    END AS `Call Priority`,
    DATEDIFF(lfu.next_follow_up, CURDATE()) AS `Follow-up Due (Days)`,
    lfu.next_follow_up AS `Follow-up Date`,
    CASE
        WHEN acg.last_grooming_visit IS NULL THEN 'No Grooming History'
        WHEN acg.last_grooming_visit BETWEEN DATE_SUB(CURDATE(), INTERVAL 90 DAY)
             AND DATE_SUB(CURDATE(), INTERVAL 45 DAY) THEN 'Dormant (45-90 days)'
        WHEN acg.last_grooming_visit < DATE_SUB(CURDATE(), INTERVAL 90 DAY) THEN 'Lost (90+ days)'
        ELSE 'Active'
    END AS `Retention Status`,
    CASE
        WHEN acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL
            THEN 'Grooming only'
        WHEN acg.last_grooming_visit IS NULL AND acf.last_food_date IS NOT NULL
            THEN 'Food only'
        WHEN acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NOT NULL
            THEN 'Both'
        ELSE 'Neither'
    END AS `Cross-sell Segment`,
    CASE
        WHEN acf.last_food_date IS NOT NULL
             AND DATEDIFF(CURDATE(), acf.last_food_date) >= 30
            THEN 'Resupply due'
        WHEN acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL
            THEN 'Cross-sell food'
        ELSE 'None'
    END AS `Food Opportunity`,
    CASE WHEN acf.last_food_date IS NOT NULL THEN 'Yes' ELSE 'No' END AS `Food Buyer`,
    acf.food_types AS `Food Types`,
    acf.last_food_date AS `Last Food Purchase`,
    DATEDIFF(CURDATE(), acf.last_food_date) AS `Days Since Food`,
    lfu.last_followup_date AS `Last Called`,
    lfu.agent_name AS `Agent`,
    lfu.call_outcome AS `Call Result`,
    lfu.quick_note AS `Notes`,
    lfu.no_follow_up_reason AS `Do Not Call Reason`,
    ch.all_channels AS `Channels`,
    NULLIF(
        TRIM(BOTH ', ' FROM CONCAT(
            IF(COALESCE(cp.has_grooming, 0) = 1, 'Grooming', ''),
            IF(COALESCE(cp.has_grooming, 0) = 1 AND COALESCE(cp.has_products, 0) = 1, ', ', ''),
            IF(COALESCE(cp.has_products, 0) = 1, 'Products', '')
        )),
        ''
    ) AS `Buys`,
    gs.last_grooming_visit AS `Last Groomed`,
    DATEDIFF(CURDATE(), gs.last_grooming_visit) AS `Days Since Groom`,
    cs.last_visit_date AS `Last Visit`,
    DATEDIFF(CURDATE(), cs.last_visit_date) AS `Days Since Visit`,
    COALESCE(gs.grooming_invoices, 0) AS `Groom Visits`,
    ROUND(COALESCE(gs.grooming_spent, 0), 0) AS `Grooming Revenue`,
    cs.total_invoices AS `Total Visits`,
    ROUND(cs.total_spent, 0) AS `Total Revenue`
FROM WorkQueueCustomers wq
JOIN CustomerChannels ch ON wq.customer = ch.customer
JOIN `tabCustomer` c ON wq.customer = c.name
LEFT JOIN CustomerStats cs ON wq.customer = cs.customer
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
    {{cross_sell_segment}} = 'All'
    OR ({{cross_sell_segment}} = 'Grooming only'
        AND acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL)
    OR ({{cross_sell_segment}} = 'Food only'
        AND acg.last_grooming_visit IS NULL AND acf.last_food_date IS NOT NULL)
    OR ({{cross_sell_segment}} = 'Both'
        AND acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NOT NULL)
    OR ({{cross_sell_segment}} = 'Neither'
        AND acg.last_grooming_visit IS NULL AND acf.last_food_date IS NULL)
  )
  AND (
    {{food_opportunity}} = 'All'
    OR ({{food_opportunity}} = 'Cross-sell food'
        AND acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL)
    OR ({{food_opportunity}} = 'Resupply due'
        AND acf.last_food_date IS NOT NULL
        AND DATEDIFF(CURDATE(), acf.last_food_date) >= 30)
    OR ({{food_opportunity}} = 'None'
        AND NOT (
            acf.last_food_date IS NOT NULL
            AND DATEDIFF(CURDATE(), acf.last_food_date) >= 30
        )
        AND NOT (acg.last_grooming_visit IS NOT NULL AND acf.last_food_date IS NULL))
  )
  AND (
    {{food_buyer}} = 'All'
    OR ({{food_buyer}} = 'Yes' AND acf.last_food_date IS NOT NULL)
    OR ({{food_buyer}} = 'No' AND acf.last_food_date IS NULL)
  )
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
ORDER BY
    CASE
        WHEN gs.last_grooming_visit IS NOT NULL
             AND lfu.next_follow_up IS NOT NULL
             AND gs.last_grooming_visit >= lfu.next_follow_up
             AND gs.last_grooming_visit >= DATE(lfu.last_followup_date) THEN 99
        WHEN lfu.next_follow_up IS NOT NULL
             AND DATEDIFF(lfu.next_follow_up, CURDATE()) = 0
             AND (lfu.no_follow_up_reason IS NULL OR lfu.no_follow_up_reason = '') THEN 1
        WHEN lfu.next_follow_up IS NOT NULL
             AND DATEDIFF(lfu.next_follow_up, CURDATE()) < 0
             AND (lfu.no_follow_up_reason IS NULL OR lfu.no_follow_up_reason = '') THEN 2
        WHEN lfu.last_followup_date IS NULL THEN 3
        WHEN lfu.next_follow_up IS NOT NULL
             AND (lfu.no_follow_up_reason IS NULL OR lfu.no_follow_up_reason = '') THEN 4
        WHEN lfu.no_follow_up_reason IS NOT NULL AND lfu.no_follow_up_reason != '' THEN 6
        ELSE 5
    END ASC,
    CASE
        WHEN lfu.next_follow_up IS NOT NULL
             AND DATEDIFF(lfu.next_follow_up, CURDATE()) < 0
             AND (lfu.no_follow_up_reason IS NULL OR lfu.no_follow_up_reason = '')
             AND NOT (
                 gs.last_grooming_visit IS NOT NULL
                 AND gs.last_grooming_visit >= lfu.next_follow_up
                 AND gs.last_grooming_visit >= DATE(lfu.last_followup_date)
             ) THEN DATEDIFF(CURDATE(), lfu.next_follow_up)
        WHEN lfu.next_follow_up IS NOT NULL
             AND DATEDIFF(lfu.next_follow_up, CURDATE()) > 0
             AND (lfu.no_follow_up_reason IS NULL OR lfu.no_follow_up_reason = '') THEN -DATEDIFF(lfu.next_follow_up, CURDATE())
        ELSE DATEDIFF(CURDATE(), COALESCE(gs.last_grooming_visit, cs.last_visit_date))
    END DESC;""")

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
    "cross_sell_segment": {
        "id": CROSS_SELL_SEGMENT_ID,
        "name": "cross_sell_segment",
        "display-name": "Cross-sell Segment",
        "type": "text",
        "default": "All",
    },
    "food_opportunity": {
        "id": FOOD_OPPORTUNITY_ID,
        "name": "food_opportunity",
        "display-name": "Food Opportunity",
        "type": "text",
        "default": "All",
    },
    "food_buyer": {
        "id": FOOD_BUYER_ID,
        "name": "food_buyer",
        "display-name": "Food Buyer",
        "type": "text",
        "default": "All",
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
    {
        "id": CROSS_SELL_SEGMENT_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "cross_sell_segment"]],
        "name": "Cross-sell Segment",
        "slug": "cross_sell_segment",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Grooming only"],
                ["Food only"],
                ["Both"],
                ["Neither"],
            ]
        },
    },
    {
        "id": FOOD_OPPORTUNITY_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "food_opportunity"]],
        "name": "Food Opportunity",
        "slug": "food_opportunity",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Cross-sell food"],
                ["Resupply due"],
                ["None"],
            ]
        },
    },
    {
        "id": FOOD_BUYER_ID,
        "type": "string/=",
        "target": ["variable", ["template-tag", "food_buyer"]],
        "name": "Food Buyer",
        "slug": "food_buyer",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [["All"], ["Yes"], ["No"]]},
    },
]

DASHBOARD_PARAMS = [
    {
        "name": "Date",
        "slug": "date",
        "id": DASH_DATE_ID,
        "type": "date/range",
        "sectionId": "date",
        "default": "past3months~",
    },
    {
        "name": "Channel",
        "slug": "followup_channel",
        "id": CHANNEL_ID,
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
        "id": PROFILE_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [["All"], ["Grooming only"], ["Products only"], ["Grooming & Products"]]
        },
    },
    {
        "name": "Cohort",
        "slug": "followup_cohort",
        "id": COHORT_ID,
        "type": "string/=",
        "sectionId": "string",
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
        "name": "Show Closed Won",
        "slug": "followup_show_closed_won",
        "id": SHOW_CLOSED_WON_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "Hide",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [["Hide"], ["Show"]]},
    },
    {
        "name": "Cross-sell Segment",
        "slug": "followup_cross_sell_segment",
        "id": CROSS_SELL_SEGMENT_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Grooming only"],
                ["Food only"],
                ["Both"],
                ["Neither"],
            ]
        },
    },
    {
        "name": "Food Opportunity",
        "slug": "followup_food_opportunity",
        "id": FOOD_OPPORTUNITY_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["All"],
                ["Cross-sell food"],
                ["Resupply due"],
                ["None"],
            ]
        },
    },
    {
        "name": "Food Buyer",
        "slug": "followup_food_buyer",
        "id": FOOD_BUYER_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "All",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {"values": [["All"], ["Yes"], ["No"]]},
    },
    {
        "name": "Period",
        "slug": "agent_period",
        "id": AGENT_DATE_ID,
        "type": "string/=",
        "sectionId": "string",
        "default": "Today",
        "values_query_type": "list",
        "values_source_type": "static-list",
        "values_source_config": {
            "values": [
                ["Today"],
                ["Yesterday"],
                ["Past 7 days"],
                ["Past 30 days"],
                ["This month"],
            ]
        },
    },
]

PARAMETER_MAPPINGS = [
    {"parameter_id": CHANNEL_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "channel"]]},
    {"parameter_id": PROFILE_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "purchase_profile"]]},
    {"parameter_id": COHORT_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "cohort"]]},
    {"parameter_id": SHOW_CLOSED_WON_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "show_closed_won"]]},
    {"parameter_id": CROSS_SELL_SEGMENT_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "cross_sell_segment"]]},
    {"parameter_id": FOOD_OPPORTUNITY_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "food_opportunity"]]},
    {"parameter_id": FOOD_BUYER_ID, "card_id": CARD_ID, "target": ["variable", ["template-tag", "food_buyer"]]},
]

INLINE_PARAMETERS = [
    CHANNEL_ID,
    PROFILE_ID,
    COHORT_ID,
    SHOW_CLOSED_WON_ID,
    CROSS_SELL_SEGMENT_ID,
    FOOD_OPPORTUNITY_ID,
    FOOD_BUYER_ID,
]

VIZ = {
    "table.columns": [
        {"name": "Customer ID", "enabled": True},
        {"name": "Customer", "enabled": True},
        {"name": "Call Priority", "enabled": True},
        {"name": "Follow-up Due (Days)", "enabled": True},
        {"name": "Follow-up Date", "enabled": True},
        {"name": "Retention Status", "enabled": True},
        {"name": "Cross-sell Segment", "enabled": True},
        {"name": "Food Opportunity", "enabled": True},
        {"name": "Food Buyer", "enabled": True},
        {"name": "Food Types", "enabled": True},
        {"name": "Last Food Purchase", "enabled": True},
        {"name": "Days Since Food", "enabled": True},
        {"name": "Last Called", "enabled": True},
        {"name": "Agent", "enabled": True},
        {"name": "Call Result", "enabled": True},
        {"name": "Notes", "enabled": True},
        {"name": "Do Not Call Reason", "enabled": True},
        {"name": "Channels", "enabled": True},
        {"name": "Buys", "enabled": True},
        {"name": "Last Groomed", "enabled": True},
        {"name": "Days Since Groom", "enabled": True},
        {"name": "Last Visit", "enabled": True},
        {"name": "Days Since Visit", "enabled": True},
        {"name": "Groom Visits", "enabled": True},
        {"name": "Grooming Revenue", "enabled": True},
        {"name": "Total Visits", "enabled": True},
        {"name": "Total Revenue", "enabled": True},
    ],
    "table.column_formatting": [
        {
            "id": 1,
            "type": "single",
            "operator": "=",
            "value": "Due Today",
            "columns": ["Call Priority"],
            "color": "#3D5A3D",
            "highlight_row": False,
        },
        {
            "id": 2,
            "type": "single",
            "operator": "=",
            "value": "Overdue",
            "columns": ["Call Priority"],
            "color": "#5A3535",
            "highlight_row": False,
        },
        {
            "id": 3,
            "type": "single",
            "operator": "=",
            "value": "Never Called",
            "columns": ["Call Priority"],
            "color": "#2D3D4A",
            "highlight_row": False,
        },
        {
            "id": 4,
            "type": "single",
            "operator": "=",
            "value": "Cross-sell food",
            "columns": ["Food Opportunity"],
            "color": "#3D4A5A",
            "highlight_row": False,
        },
        {
            "id": 5,
            "type": "single",
            "operator": "=",
            "value": "Resupply due",
            "columns": ["Food Opportunity"],
            "color": "#5A4A3D",
            "highlight_row": False,
        },
    ],
    "column_settings": {
        '["name","Customer ID"]': {
            "click_behavior": {
                "type": "link",
                "linkType": "url",
                "linkTemplate": "https://dashboard.masterpet.co.in/crm/customer/{{Customer ID}}",
            },
        },
        '["name","Follow-up Date"]': {
            "date_style": "MMMM D, YYYY",
        },
        '["name","Last Food Purchase"]': {
            "date_style": "MMMM D, YYYY",
        },
        '["name","Last Groomed"]': {
            "date_style": "MMMM D, YYYY",
        },
        '["name","Last Visit"]': {
            "date_style": "MMMM D, YYYY",
        },
        '["name","Last Called"]': {
            "date_style": "MMMM D, YYYY",
            "time_enabled": "minutes",
        },
        '["name","Grooming Revenue"]': {
            "number_style": "currency",
            "currency": "INR",
            "decimals": 0,
        },
        '["name","Total Revenue"]': {
            "number_style": "currency",
            "currency": "INR",
            "decimals": 0,
        },
    },
}


def esc(s: str) -> str:
    return s.replace("'", "''")


def main() -> None:
    dq = {
        "lib/type": "mbql/query",
        "database": 2,
        "stages": [{"lib/type": "mbql.stage/native", "native": SQL, "template-tags": TEMPLATE_TAGS}],
    }
    sql = f"""
UPDATE report_card
SET name = 'Customer Follow-ups & Purchase Profile',
    display = 'table',
    dataset_query = '{esc(json.dumps(dq))}',
    parameters = '{esc(json.dumps(CARD_PARAMETERS))}',
    visualization_settings = '{esc(json.dumps(VIZ))}',
    updated_at = NOW()
WHERE id = {CARD_ID};

UPDATE report_dashboard
SET parameters = '{esc(json.dumps(DASHBOARD_PARAMS))}', updated_at = NOW()
WHERE id = {DASHBOARD_ID};

UPDATE report_dashboardcard
SET parameter_mappings = '{esc(json.dumps(PARAMETER_MAPPINGS))}',
    inline_parameters = '{esc(json.dumps(INLINE_PARAMETERS))}',
    updated_at = NOW()
WHERE dashboard_id = {DASHBOARD_ID} AND card_id = {CARD_ID};
"""
    subprocess.run(
        ["docker", "exec", "metabase-postgres", "psql", "-U", "metabase", "-d", "metabase", "-c", sql],
        check=True,
    )
    print("Updated follow-up table: food cross-sell columns + filters")


if __name__ == "__main__":
    main()
