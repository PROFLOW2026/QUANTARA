"""Home dashboard reason-code presentation — no raw internal snake_case leaks."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DISPLAY_TEXT = REPO_ROOT / "apps" / "web" / "lib" / "display-text.ts"
HE_JSON = REPO_ROOT / "apps" / "web" / "messages" / "he.json"
HOME_COMPONENTS = [
    REPO_ROOT / "apps" / "web" / "components" / "dashboard" / "HomeDashboard.tsx",
    REPO_ROOT / "apps" / "web" / "components" / "trading" / "LatestDecisionsPanel.tsx",
    REPO_ROOT / "apps" / "web" / "components" / "trading" / "SignalCard.tsx",
    REPO_ROOT / "apps" / "web" / "components" / "trading" / "ExposureRiskSummaryCards.tsx",
    REPO_ROOT / "apps" / "web" / "components" / "trading" / "ActiveAssetsPanel.tsx",
]


def test_breakout_already_consumed_has_hebrew_mapping():
    he = json.loads(HE_JSON.read_text(encoding="utf-8"))
    assert "orb_breakout_already_consumed" in he["signals"]
    assert "הפריצה" in he["signals"]["orb_breakout_already_consumed"]

    src = DISPLAY_TEXT.read_text(encoding="utf-8")
    assert "breakout_already_consumed" in src
    assert "orb_breakout_already_consumed" in src


def test_home_components_do_not_render_raw_decision_message():
    for path in HOME_COMPONENTS:
        text = path.read_text(encoding="utf-8")
        assert "decision.message" not in text or "translateSignalReason" in text
        assert "row.message" not in text or "translateSignalReason" in text


def test_exposure_cards_do_not_default_missing_to_zero():
    cards = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "ExposureRiskSummaryCards.tsx"
    ).read_text(encoding="utf-8")
    assert "summary?.open_exposure ?? 0" not in cards
    assert "summary?.open_risk_usd ?? 0" not in cards
    assert "formatCurrencyOrUnavailable" in cards


def test_internal_snake_case_reasons_not_returned_raw():
    src = DISPLAY_TEXT.read_text(encoding="utf-8")
    assert re.search(r"\^\[a-z\]\[a-z0-9_\]\+\$", src)


def test_provider_health_hebrew_status_mappings():
    he = json.loads(HE_JSON.read_text(encoding="utf-8"))
    home = he["home"]
    assert home["provider_status_healthy"] == "תקין"
    assert home["provider_status_temp_error"] == "תקלה זמנית"
    assert home["provider_status_quota_blocked"] == "חסום במכסה"
    assert home["provider_status_conservation"] == "מצב חיסכון"
    assert home["provider_status_exhausted"] == "מכסה נוצלה"
    assert home["provider_error_temp"] == "תקלה זמנית אצל הספק"
    assert home["provider_error_quota"] == "מכסת הנתונים נוצלה"
    assert home["provider_details"] == "פרטים"


def test_provider_health_card_hides_raw_errors_by_default():
    panel = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "ActiveAssetsPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "translateProviderStatus" in panel
    assert "providerHasTechnicalDetails" in panel
    assert "showDetails" in panel
    assert "health.last_error" not in panel.replace("health?.last_error", "")
    assert "twelvedata.com/pricing" not in panel


def test_provider_error_translation_helpers_exist():
    src = DISPLAY_TEXT.read_text(encoding="utf-8")
    assert "translateProviderError" in src
    assert "translateProviderStatus" in src
    assert "formatProviderUsageLine" in src
    assert "backend request timeout" in src
    assert "twelvedata.com" in src
