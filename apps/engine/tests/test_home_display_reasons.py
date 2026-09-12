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
    REPO_ROOT / "apps" / "web" / "components" / "trading" / "AssetChartModal.tsx",
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


def test_provider_health_card_does_not_render_technical_details():
    panel = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "ActiveAssetsPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "translateProviderStatus" in panel
    assert "providerHasTechnicalDetails" not in panel
    assert "provider_details" not in panel
    assert "showDetails" not in panel
    assert "last_error" not in panel
    assert "twelvedata.com/pricing" not in panel


def test_provider_error_translation_helpers_exist():
    src = DISPLAY_TEXT.read_text(encoding="utf-8")
    assert "translateProviderError" in src
    assert "translateProviderStatus" in src
    assert "formatProviderUsageLine" in src
    assert "backend request timeout" in src
    assert "twelvedata.com" in src


def test_asset_data_status_visual_state_labels():
    he = json.loads(HE_JSON.read_text(encoding="utf-8"))
    home = he["home"]
    assert home["asset_status_healthy"] == "תקין"
    assert home["asset_status_session_closed"] == "סגור — נתוני סשן אחרון"
    assert home["asset_status_data_error"] == "שגיאת נתונים"
    assert home["asset_status_updating"] == "מעדכן"
    assert home["asset_status_waiting_data"] == "ממתין לנתון"

    src = DISPLAY_TEXT.read_text(encoding="utf-8")
    panel = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "ActiveAssetsPanel.tsx"
    ).read_text(encoding="utf-8")
    assert "resolveAssetDataStatusPresentation" in src
    assert "resolveAssetDataStatusPresentation" in panel
    assert 'variant: "warning"' in src or 'variant: "warning"' in panel
    assert "session_closed" in panel
    assert "last_candle" in panel


def test_asset_chart_modal_hebrew_strings_and_canonical_candles():
    he = json.loads(HE_JSON.read_text(encoding="utf-8"))
    home = he["home"]
    assert home["asset_chart_loading"] == "טוען נתוני גרף..."
    assert home["asset_chart_error"] == "שגיאה בטעינת נתוני הגרף"
    assert home["asset_chart_no_data"] == "אין מספיק נתונים להצגה"
    assert home["asset_chart_no_open_position"] == "פוזיציה פתוחה: אין"

    modal = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "AssetChartModal.tsx"
    ).read_text(encoding="utf-8")
    panel = (
        REPO_ROOT / "apps" / "web" / "components" / "trading" / "ActiveAssetsPanel.tsx"
    ).read_text(encoding="utf-8")
    dashboard = (
        REPO_ROOT / "apps" / "web" / "components" / "dashboard" / "HomeDashboard.tsx"
    ).read_text(encoding="utf-8")

    assert "api.getCandles" in modal
    assert "instrument_id: asset.db_symbol" in modal
    assert "TradingView" not in modal
    assert 'useState("15m")' in modal
    assert '"5m"' in modal and '"15m"' in modal and '"1h"' in modal
    assert "AssetSymbolButton" in panel
    assert "AssetChartModal" in panel
    assert "setChartAsset" in panel
    assert "getCandles" not in panel
    assert "getCandles" not in dashboard
    assert "assetDecisions={assetDecisions}" in dashboard
    assert "CandlestickChart" in modal
    assert "ChartExecutionMarker" in (
        REPO_ROOT / "apps" / "web" / "components" / "charts" / "chart-types.ts"
    ).read_text(encoding="utf-8")
    assert "candleCache.current.clear()" in modal
    assert "CHART_CANDLE_LIMIT = 120" in modal
    assert "timeframe={timeframe}" in modal

    chart = (
        REPO_ROOT / "apps" / "web" / "components" / "charts" / "CandlestickChart.tsx"
    ).read_text(encoding="utf-8")
    assert "formatChartAxisTick" in chart
    assert "buildDayBoundaryTimes" in chart
    assert "he-IL" not in chart or "Intl.DateTimeFormat" not in chart


def test_candles_route_uses_list_recent_candles():
    routes = (
        REPO_ROOT / "apps" / "engine" / "quantara_engine" / "api" / "routes.py"
    ).read_text(encoding="utf-8")
    candles_block = routes.split('@router.get("/candles")', 1)[1].split(
        '@router.get("/candles/latest")', 1
    )[0]
    assert "list_recent_candles" in candles_block
    assert "list_candles(instrument.id, timeframe, limit=limit)" not in candles_block
