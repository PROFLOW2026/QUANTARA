"""Final navigation / HUD UI contract — sidebar removed, bottom nav unified."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_LAYOUT = REPO_ROOT / "apps" / "web" / "components" / "layout"
HE_JSON = REPO_ROOT / "apps" / "web" / "messages" / "he.json"

BOTTOM_NAV_ROUTES = [
    "/portfolio-comparison",
    "/positions",
    "/decisions",
    "/analytics",
    "/settings",
]

MORE_MENU_ROUTES = [
    "/portfolio",
    "/portfolios",
    "/journal",
    "/strategies",
    "/backtests",
    "/experiments",
    "/market/gold",
]

FORMER_SIDEBAR_ROUTES = [
    "/",
    *BOTTOM_NAV_ROUTES,
    *MORE_MENU_ROUTES,
]


def test_desktop_sidebar_removed():
    assert not (WEB_LAYOUT / "Sidebar.tsx").exists()
    shell = (WEB_LAYOUT / "DashboardShell.tsx").read_text(encoding="utf-8")
    assert "Sidebar" not in shell
    assert "TopHeader" in shell
    assert "BottomNavigation" in shell


def test_bottom_nav_has_six_primary_actions_without_home():
    nav = (WEB_LAYOUT / "BottomNavigation.tsx").read_text(encoding="utf-8")
    he = json.loads(HE_JSON.read_text(encoding="utf-8"))
    assert he["nav"]["more"] == "עוד"
    assert 'href: "/"' not in nav or "nav.home" not in nav
    assert "nav.home" not in nav
    for route in BOTTOM_NAV_ROUTES:
        assert route in nav
    assert "nav.more" in nav


def test_all_former_sidebar_routes_preserved_in_nav():
    nav = (WEB_LAYOUT / "BottomNavigation.tsx").read_text(encoding="utf-8")
    shell = (WEB_LAYOUT / "TopHeader.tsx").read_text(encoding="utf-8")
    for route in FORMER_SIDEBAR_ROUTES:
        if route == "/":
            assert 'href="/"' in shell
            continue
        assert route in nav


def test_main_content_has_bottom_padding_for_fixed_nav():
    shell = (WEB_LAYOUT / "DashboardShell.tsx").read_text(encoding="utf-8")
    assert "pb-20" in shell
    assert "lg:pb-0" not in shell
    assert "lg:w-60" not in shell
