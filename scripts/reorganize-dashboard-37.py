#!/usr/bin/env python3
"""Single source of truth for dashboard 37 layout and organization."""

from __future__ import annotations

import importlib.util
import json
import secrets
import subprocess
from pathlib import Path

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

_spec3 = importlib.util.spec_from_file_location(
    "today_fups",
    Path(__file__).with_name("add-todays-followups-table.py"),
)
_today_fups = importlib.util.module_from_spec(_spec3)
assert _spec3.loader is not None
_spec3.loader.exec_module(_today_fups)

_spec4 = importlib.util.spec_from_file_location(
    "repeat_customers",
    Path(__file__).with_name("add-repeat-customer-metrics.py"),
)
_repeat = importlib.util.module_from_spec(_spec4)
assert _spec4.loader is not None
_spec4.loader.exec_module(_repeat)

DASHBOARD_ID = _kpi.DASHBOARD_ID

OVERVIEW_TAB_NAME = "Overview"
EXPENSES_TAB_NAME = "Expenses"

TAB_OVERVIEW = 0
TAB_EXPENSES = 0

# --- Grid rows (24-column Metabase grid) ---
ROW_HEADING_SNAPSHOT = 0
ROW_HERO = 1
ROW_HERO_FOLLOWUPS = 4
ROW_DETAIL = 7
DETAIL_TABLE_HEIGHT = 6
PAST_7_DAYS_DETAIL_HEIGHT = 7
ROW_AVG_GROOMING_REVENUE = 14
ROW_HEADING_SALES = 20
ROW_MONTHLY_PAIR = 21
ROW_RETENTION = 28
ROW_COHORT = 35
ROW_WEEKLY_DAILY = 47
ROW_GROOM_ROLLING = 53
ROW_HEADING_FOLLOWUP = 60
ROW_QUEUE_KPI = 61
ROW_TODAY_FOLLOWUPS = 65
ROW_PROGRESS = 71
ROW_FOLLOWUP_TABLE = 78
ROW_AGENT_ACTIVITY = 90

HEADINGS = [
    (ROW_HEADING_SNAPSHOT, "Business Snapshot"),
    (ROW_HEADING_SALES, "Sales & Growth Trends"),
    (ROW_HEADING_FOLLOWUP, "Follow-up Operations"),
]

# Cards removed from dashboard (duplicate or demoted)
REMOVED_CARD_IDS = (156, 158, 159, 161, 165, 166, 169, 191, 194)

# Shorter display names for cluttered chart titles
CARD_RENAMES = {
    157: "Monthly Sales by Channel",
    160: "Monthly Groomings by Channel",
    173: "30-Day Rolling Customers & Groomings",
    196: "Avg Daily Grooming Revenue by Channel",
    197: "Avg Daily Revenue per Grooming by Channel",
}

HERO_SCALAR_HEIGHT = 3
QUEUE_SCALAR_SIZE = (4, 4)


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


def psql_rows(sql: str) -> list[str]:
    r = subprocess.run(
        ["docker", "exec", "metabase-postgres", "psql", "-U", "metabase", "-d", "metabase", "-t", "-A", "-c", sql],
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def ensure_dashboard_tabs() -> None:
    """Create Overview + Expenses tabs; assign existing dashcards to Overview."""
    global TAB_OVERVIEW, TAB_EXPENSES

    rows = psql_rows(
        f"SELECT id, name, position FROM dashboard_tab WHERE dashboard_id = {DASHBOARD_ID} ORDER BY position;"
    )
    tabs = {}
    for line in rows:
        tab_id, name, position = line.split("|")
        tabs[name] = int(tab_id)

    if OVERVIEW_TAB_NAME not in tabs:
        TAB_OVERVIEW = int(
            psql(
                f"""
INSERT INTO dashboard_tab (dashboard_id, name, position, entity_id, created_at, updated_at)
VALUES ({DASHBOARD_ID}, '{esc(OVERVIEW_TAB_NAME)}', 0, '{entity_id()}', NOW(), NOW())
RETURNING id;
"""
            )
        )
    else:
        TAB_OVERVIEW = tabs[OVERVIEW_TAB_NAME]

    if EXPENSES_TAB_NAME not in tabs:
        TAB_EXPENSES = int(
            psql(
                f"""
INSERT INTO dashboard_tab (dashboard_id, name, position, entity_id, created_at, updated_at)
VALUES ({DASHBOARD_ID}, '{esc(EXPENSES_TAB_NAME)}', 1, '{entity_id()}', NOW(), NOW())
RETURNING id;
"""
            )
        )
    else:
        TAB_EXPENSES = tabs[EXPENSES_TAB_NAME]

    psql(
        f"UPDATE dashboard_tab SET position = 0, updated_at = NOW() "
        f"WHERE id = {TAB_OVERVIEW} AND dashboard_id = {DASHBOARD_ID};"
    )
    psql(
        f"UPDATE dashboard_tab SET position = 1, updated_at = NOW() "
        f"WHERE id = {TAB_EXPENSES} AND dashboard_id = {DASHBOARD_ID};"
    )
    psql(
        f"UPDATE report_dashboardcard SET dashboard_tab_id = {TAB_OVERVIEW}, updated_at = NOW() "
        f"WHERE dashboard_id = {DASHBOARD_ID} AND dashboard_tab_id IS NULL;"
    )


def filter_mappings(card_id: int, full: bool = False) -> list[dict]:
    source = _upd.PARAMETER_MAPPINGS if full else _kpi.PARAMETER_MAPPINGS
    return [{**m, "card_id": card_id} for m in source]


def upsert_heading(row: int, text: str, tab_id: int | None = None) -> None:
    tab_id = tab_id if tab_id is not None else TAB_OVERVIEW
    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} "
        f"AND dashboard_tab_id = {tab_id} AND card_id IS NULL "
        f"AND visualization_settings::text LIKE '%{esc(text)}%' LIMIT 1;"
    )
    viz = json.dumps(
        {
            "column_settings": None,
            "dashcard.background": False,
            "text": text,
            "virtual_card": {
                "archived": False,
                "dataset_query": {},
                "display": "heading",
                "name": None,
                "visualization_settings": {},
            },
        }
    )
    if existing:
        psql(
            f"UPDATE report_dashboardcard SET row = {row}, col = 0, size_x = 24, size_y = 1, "
            f"dashboard_tab_id = {tab_id}, visualization_settings = '{esc(viz)}', updated_at = NOW() "
            f"WHERE id = {existing};"
        )
        return
    psql(
        f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, dashboard_id, dashboard_tab_id, parameter_mappings,
    visualization_settings, entity_id
) VALUES (
    24, 1, {row}, 0, {DASHBOARD_ID}, {tab_id}, '[]',
    '{esc(viz)}', '{entity_id()}'
);
"""
    )


def place_card(
    card_id: int,
    row: int,
    col: int,
    size_x: int,
    size_y: int,
    mappings: list[dict] | None = None,
    inline_parameters: list[str] | None = None,
    viz_settings: dict | None = None,
    tab_id: int | None = None,
) -> None:
    tab_id = tab_id if tab_id is not None else TAB_OVERVIEW
    existing = psql(
        f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} "
        f"AND card_id = {card_id} AND dashboard_tab_id = {tab_id} LIMIT 1;"
    )
    if not existing:
        existing = psql(
            f"SELECT id FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} "
            f"AND card_id = {card_id} LIMIT 1;"
        )
    if existing:
        extra_sql = f", dashboard_tab_id = {tab_id}"
        if mappings is not None:
            extra_sql += f", parameter_mappings = '{esc(json.dumps(mappings))}'"
        if inline_parameters is not None:
            extra_sql += f", inline_parameters = '{esc(json.dumps(inline_parameters))}'"
        if viz_settings is not None:
            extra_sql += f", visualization_settings = '{esc(json.dumps(viz_settings))}'"
        psql(
            f"UPDATE report_dashboardcard SET row = {row}, col = {col}, "
            f"size_x = {size_x}, size_y = {size_y}{extra_sql}, updated_at = NOW() WHERE id = {existing};"
        )
        return
    mappings_json = esc(json.dumps(mappings if mappings is not None else []))
    inline_json = esc(json.dumps(inline_parameters if inline_parameters is not None else []))
    viz_json = esc(json.dumps(viz_settings if viz_settings is not None else {}))
    psql(
        f"""
INSERT INTO report_dashboardcard (
    size_x, size_y, row, col, card_id, dashboard_id, dashboard_tab_id,
    parameter_mappings, visualization_settings, entity_id, inline_parameters
) VALUES (
    {size_x}, {size_y}, {row}, {col}, {card_id}, {DASHBOARD_ID}, {tab_id},
    '{mappings_json}', '{viz_json}', '{entity_id()}', '{inline_json}'
);
"""
    )


def remove_cards(card_ids: tuple[int, ...]) -> None:
    ids = ",".join(str(i) for i in card_ids)
    psql(f"DELETE FROM report_dashboardcard WHERE dashboard_id = {DASHBOARD_ID} AND card_id IN ({ids});")


def rename_cards() -> None:
    for card_id, name in CARD_RENAMES.items():
        psql(f"UPDATE report_card SET name = '{esc(name)}', updated_at = NOW() WHERE id = {card_id};")


def reorder_dashboard_filters() -> None:
    """Date + Channel first; follow-up filters grouped; Period last."""
    params = json.loads(psql(f"SELECT parameters::text FROM report_dashboard WHERE id = {DASHBOARD_ID};"))
    by_slug = {p["slug"]: p for p in params}
    order = [
        "date",
        "followup_day",
        "followup_channel",
        "followup_profile",
        "followup_cohort",
        "followup_show_closed_won",
        "followup_cross_sell_segment",
        "followup_food_opportunity",
        "followup_food_buyer",
        "agent_period",
    ]
    reordered = [by_slug[slug] for slug in order if slug in by_slug]
    reordered.extend(p for p in params if p["slug"] not in order)
    psql(
        f"UPDATE report_dashboard SET parameters = '{esc(json.dumps(reordered))}', updated_at = NOW() "
        f"WHERE id = {DASHBOARD_ID};"
    )


def apply_layout() -> None:
    revenue_hero = [
        (183, 0, 6),   # MTD Revenue
        (184, 6, 6),   # MTD Groomings
        (185, 12, 6),  # Avg Daily Revenue
        (186, 18, 6),  # Projected Month-end Sales
    ]
    for card_id, col, size_x in revenue_hero:
        place_card(card_id, ROW_HERO, col, size_x, HERO_SCALAR_HEIGHT)

    followup_hero = [
        (190, 0, 6),   # Followups Done Today
        (178, 6, 6),   # Overdue Followups
        (177, 12, 6),  # MTD Followups Completed
        (182, 18, 6),  # Avg Daily Followups Completed
    ]
    for card_id, col, size_x in followup_hero:
        if card_id == 178:
            place_card(
                card_id,
                ROW_HERO_FOLLOWUPS,
                col,
                size_x,
                HERO_SCALAR_HEIGHT,
                filter_mappings(card_id),
            )
        elif card_id == 190:
            place_card(
                card_id,
                ROW_HERO_FOLLOWUPS,
                col,
                size_x,
                HERO_SCALAR_HEIGHT,
                _today_fups.followup_day_mappings(card_id),
                [],
            )
        else:
            place_card(card_id, ROW_HERO_FOLLOWUPS, col, size_x, HERO_SCALAR_HEIGHT)

    place_card(179, ROW_DETAIL, 0, 8, DETAIL_TABLE_HEIGHT)
    place_card(180, ROW_DETAIL, 8, 8, DETAIL_TABLE_HEIGHT)
    place_card(188, ROW_DETAIL, 16, 8, PAST_7_DAYS_DETAIL_HEIGHT)  # Past 7 Days — Daily Sales Detail

    place_card(196, ROW_AVG_GROOMING_REVENUE, 0, 12, 6)
    place_card(197, ROW_AVG_GROOMING_REVENUE, 12, 12, 6)

    place_card(157, ROW_MONTHLY_PAIR, 0, 12, 7)
    place_card(181, ROW_MONTHLY_PAIR, 12, 12, 7)

    place_card(192, ROW_RETENTION, 0, 24, 7, _repeat.channel_mappings(192))

    place_card(
        193,
        ROW_COHORT,
        0,
        24,
        12,
        _repeat.channel_mappings(193),
        viz_settings=_repeat.cohort_dashcard_viz(),
    )

    place_card(187, ROW_WEEKLY_DAILY, 0, 24, 6)   # Past 7 days daily sales chart
    place_card(160, ROW_GROOM_ROLLING, 0, 12, 7)
    place_card(173, ROW_GROOM_ROLLING, 12, 12, 7)

    qx, qy = QUEUE_SCALAR_SIZE
    queue = [
        (167, 0, 5),   # Never called
        (168, 5, 5),   # Future calls
        (170, 10, 5),  # Food cross-sell
        (171, 15, 5),  # Food resupply
        (172, 20, 4),  # Total in queue
    ]
    for card_id, col, qx in queue:
        place_card(card_id, ROW_QUEUE_KPI, col, qx, qy, filter_mappings(card_id))

    place_card(
        189,
        ROW_TODAY_FOLLOWUPS,
        0,
        24,
        6,
        _today_fups.followup_day_mappings(189),
        [],
    )

    place_card(175, ROW_PROGRESS, 0, 16, 7, filter_mappings(175))
    place_card(176, ROW_PROGRESS, 16, 8, 7)
    place_card(164, ROW_FOLLOWUP_TABLE, 0, 24, 12, filter_mappings(164, full=True))
    place_card(174, ROW_AGENT_ACTIVITY, 0, 24, 6)


def apply_expenses_tab_layout() -> None:
    upsert_heading(0, "Expenses", tab_id=TAB_EXPENSES)


def main() -> None:
    ensure_dashboard_tabs()
    for row, text in HEADINGS:
        upsert_heading(row, text)
    remove_cards(REMOVED_CARD_IDS)
    _repeat.remove_cohort_drill_dashboard_params()
    rename_cards()
    _today_fups.sync_dashboard_followup_day_param()
    reorder_dashboard_filters()
    apply_layout()
    apply_expenses_tab_layout()
    print(
        f"Done: dashboard {DASHBOARD_ID} reorganized "
        f"(tabs: {OVERVIEW_TAB_NAME}={TAB_OVERVIEW}, {EXPENSES_TAB_NAME}={TAB_EXPENSES})"
    )


if __name__ == "__main__":
    main()
