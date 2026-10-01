"""V3.2 P2 rule families via canonical pipeline (closed-bar signal, N+1 execution)."""

from __future__ import annotations

from decimal import Decimal

from quantara_engine.domain.types import Signal, SignalAction, StrategyContext
from quantara_engine.market_data.registry import get_asset
from quantara_engine.market_data.sessions import session_allows_entries
from quantara_engine.research.v3_1.rule_lab import entry_mask_series as entry_mask_v31
from quantara_engine.research.v3_2.rule_lab import SUPPORTED_FAMILIES, entry_mask_series as entry_mask_v32
from quantara_engine.strategies.base import BaseStrategy
from quantara_engine.strategies.common.indicators import atr, candles_to_df, to_decimal


class V31RuleReplayV1(BaseStrategy):
    @classmethod
    def strategy_id(cls) -> str:
        return "v32-p2-live-sim"

    @classmethod
    def version(cls) -> str:
        return "1.0.0"

    @classmethod
    def name(cls) -> str:
        return "V3.2 P2 Live Sim"

    @classmethod
    def description(cls) -> str:
        return "V3.2 P2 portfolio rule families (rsi_divergence_mr, ema_pullback_continue, …)."

    @classmethod
    def supported_instruments(cls) -> list[str]:
        from quantara_engine.market_data.active_universe import list_active_db_symbols

        return list(list_active_db_symbols())

    @classmethod
    def supported_timeframes(cls) -> list[str]:
        return ["5m", "15m", "1h"]

    @classmethod
    def default_parameters(cls) -> dict:
        return {
            "family": "atr_trend",
            "trade_direction": "long",
            "atr_sl": 1.5,
            "atr_tp": 2.5,
        }

    @classmethod
    def parameters_schema(cls) -> dict:
        return {"type": "object", "properties": cls.default_parameters()}

    @classmethod
    def risk_profile_compatibility(cls) -> list[str]:
        return ["balanced", "conservative", "aggressive"]

    def evaluate(self, candles: list, context: StrategyContext) -> Signal:
        params = {**self.default_parameters(), **context.parameters}
        if len(candles) < 120:
            return Signal(action=SignalAction.HOLD, reason="insufficient_history")
        # Current bar is still forming in live; in backtest run_all includes it — signal only from prior completed bar.
        closed = candles[:-1]
        if len(closed) < 100:
            return Signal(action=SignalAction.HOLD, reason="insufficient_closed")
        signal_candle = closed[-1]
        df = candles_to_df(closed)
        df.index = __import__("pandas").to_datetime([c.timestamp for c in closed], utc=True)
        family = str(params["family"])
        direction = str(params["trade_direction"])
        symbol = str(params.get("symbol") or context.runtime.get("symbol") or "AMD")
        asset = get_asset(symbol)

        def session_open(ts):
            sessions = asset.trading_sessions or {}
            return session_allows_entries(sessions, ts)

        use_session = family in ("session_breakout", "vwap_session_revert", "orb_continuation")
        if family in SUPPORTED_FAMILIES:
            mask = entry_mask_v32(
                family,
                df,
                direction=direction,
                parameters=params,
                session_open=session_open if use_session else None,
            )
        else:
            mask = entry_mask_v31(
                family,
                df,
                direction=direction,
                parameters=params,
                session_open=session_open if family == "session_breakout" else None,
            )
        if not bool(mask.iloc[-1]):
            return Signal(action=SignalAction.HOLD, reason="no_setup")
        atr_val = float(atr(df, 14).iloc[-1])
        close = float(df["close"].iloc[-1])
        sl_mult = float(params.get("atr_sl", 1.5))
        tp_mult = float(params.get("atr_tp", 2.5))
        meta = {
            "signal_candle_timestamp": signal_candle.timestamp.isoformat(),
            "execution_model": "canonical_n_plus_1_open",
            "family": family,
        }
        if direction == "long":
            return Signal(
                action=SignalAction.BUY,
                reason=f"v31_{family}_long",
                suggested_sl=to_decimal(close - atr_val * sl_mult),
                suggested_tp=to_decimal(close + atr_val * tp_mult),
                metadata=meta,
            )
        return Signal(
            action=SignalAction.SELL,
            reason=f"v31_{family}_short",
            suggested_sl=to_decimal(close + atr_val * sl_mult),
            suggested_tp=to_decimal(close - atr_val * tp_mult),
            metadata=meta,
        )
