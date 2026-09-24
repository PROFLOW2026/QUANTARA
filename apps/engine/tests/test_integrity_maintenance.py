"""Periodic attribution integrity maintenance."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from quantara_engine.broker.integrity_maintenance import run_attribution_integrity_maintenance


@patch("quantara_engine.broker.integrity_maintenance.reconcile_stale_live_sim_shadows")
@patch("quantara_engine.broker.integrity_maintenance.link_orphan_lots_via_intent_chain")
def test_run_attribution_integrity_maintenance_reports_links(link_mock, reconcile_mock):
    link_mock.return_value = [{"lot_id": "a", "fill_id": "b", "strategy_position_id": "c"}]
    reconcile_mock.return_value = [{"position_id": "p1", "symbol": "ETHUSD"}]
    store = MagicMock()
    report = run_attribution_integrity_maintenance(store)
    assert report["orphan_lots_linked"] == 1
    assert report["stale_shadows_closed"] == 1
    link_mock.assert_called_once_with(store)
    reconcile_mock.assert_called_once_with(store)


@patch("quantara_engine.broker.integrity_maintenance.reconcile_stale_live_sim_shadows")
@patch("quantara_engine.broker.integrity_maintenance.link_orphan_lots_via_intent_chain")
def test_run_attribution_integrity_maintenance_noop_when_empty(link_mock, reconcile_mock):
    link_mock.return_value = []
    reconcile_mock.return_value = []
    report = run_attribution_integrity_maintenance(MagicMock())
    assert report["orphan_lots_linked"] == 0
    assert report["stale_shadows_closed"] == 0
