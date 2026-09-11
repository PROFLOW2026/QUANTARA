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
