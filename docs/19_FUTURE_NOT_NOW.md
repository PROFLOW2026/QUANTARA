# QUANTARA — Future (Not Now)

## 1. Purpose

מסמך זה מגדיר במפורש מה **מתוכנן לעתיד** אך **אסור לבנות** ב-Phase 1.
מטרה: למנוע scope creep ולשמור על ארכיטקטורה מוכנה.

## 2. Trading Modes — Future

| Mode | Description | Prerequisite |
|------|-------------|--------------|
| LIVE-MANUAL | Signal → user approval → broker | Paper validated 3+ months |
| LIVE-AUTOMATED | Full auto via broker | LIVE-MANUAL validated |

Architecture ready via: `BrokerAdapter`, `ExecutionAdapter`, `Clock`.

## 3. Broker Integration — Future

| Item | When |
|------|------|
| MT5BrokerAdapter | After paper trading stable |
| MT5 Demo account | Test execution, spread, slippage |
| Other brokers (IB, OANDA live) | Owner decision |
| Live money | Far future, explicit owner decision |

Phase 1: **PaperBrokerAdapter only.**

## 4. Instruments — Future

Architecture supports adding without structural changes:

| Instrument | Asset Class |
|------------|-------------|
| NAS100 / NASDAQ | Index |
| SPX / S&P 500 | Index |
| EUR/USD, GBP/USD | Forex |
| BTC/USD, ETH/USD | Crypto |
| Individual stocks | Stock |
| Oil, Silver | Commodity |

Adding instrument = seed data + strategy compatibility + market data adapter config.
**Not Phase 1.**

## 5. AI Layer — Future

AI as **explanation and analysis only**:

| Capability | Description |
|------------|-------------|
| Explain trade | Why entry/exit happened |
| Explain loss | What went wrong |
| Explain market | Context for current conditions |
| Explain strategy | Teach strategy logic |
| Analyze journal | Behavioral patterns |
| Performance insights | Pattern detection in results |

**Never:** AI price prediction, ML signals, neural network trading, LLM decision making.

## 6. Machine Learning — Not Planned

Explicitly out of scope unless owner explicitly requests after Phase 1:

- Neural networks
- Reinforcement learning
- Sentiment analysis trading
- Alternative data ML pipelines

## 7. Advanced Backtesting — Future

| Feature | Description |
|---------|-------------|
| Out-of-sample testing | Train/test split UI |
| Walk-forward optimization | Rolling windows |
| Monte Carlo simulation | Randomized trade order |
| Parameter optimization | Grid search with overfit warnings |
| Benchmark comparison UI | vs buy-and-hold, vs index |
| Multi-strategy backtest | Compare in one run |

Data model has foundation fields. UI and engine logic = future.

## 8. Experiments — Future

| Feature | Description |
|---------|-------------|
| Parallel strategy runs | A vs B vs C on same feed |
| Virtual portfolios per experiment | Separate P&L tracking |
| Comparison dashboard | Side-by-side metrics |
| Parameter sweep | Multiple configs |

Phase 1: `experiments` table + basic list UI only.

## 9. SaaS / Productization — Future

**Do not build unless explicitly converting to product:**

| Feature |
|---------|
| User registration |
| Login / password reset |
| MFA |
| Organizations |
| Teams |
| Role-based permissions |
| Billing / subscriptions |
| Stripe integration |
| KYC / compliance |
| Affiliate system |
| Customer support portal |
| Public onboarding |
| Multi-tenancy |
| Usage metering |
| Admin panel for users |

Phase 1 auth: simple API key / session for single owner.

## 10. UI — Future

| Feature | Phase |
|---------|-------|
| English language | After Hebrew stable |
| Light theme | Optional |
| Custom dashboard widgets | v2 |
| Mobile native app | Far future |
| Real-time WebSocket updates | When needed for live |
| Notifications (email/push) | Live trading phase |

Phase 1: Hebrew, dark theme, polling refresh.

## 11. Infrastructure — Future

| Feature | When |
|---------|------|
| Celery + Redis workers | If single process insufficient |
| Kubernetes deployment | Production scale |
| CI/CD pipeline | Team development |
| Monitoring (Grafana, Sentry) | Production |
| Automated backups | Production |
| Multi-region | SaaS scale |

Phase 1: Docker Compose local/single VPS.

## 12. Market Data — Future

| Feature | Description |
|---------|-------------|
| Tick data | HFT — not needed |
| Level 2 / order book | Advanced |
| Multiple provider failover UI | Auto-switch |
| Data interpolation | Gap filling |
| Custom data import | CSV upload |
| Fundamental data | News, economic calendar |

Phase 1: OHLC candles, single provider + mock, no gap filling.

## 13. Risk — Future

| Feature | Description |
|---------|-------------|
| Sharpe / Sortino / Calmar | Advanced metrics |
| Correlation-based limits | Multi-instrument |
| Dynamic position sizing (Kelly) | Advanced |
| Volatility regime detection | Auto-adjust risk |
| News-based halt | External events |

Phase 1: Fixed fractional sizing, static profiles.

## 14. Margin / Leverage — Future

Phase 1: simplified cash model (full notional).
Future: configurable leverage per instrument.

## 15. How to Use This Document

When developing Phase 1:

1. **See a future feature?** → Check if it's here → don't build it
2. **Architecture decision?** → Ensure it doesn't **block** future items
3. **Tempted to add "just one SaaS feature"?** → Don't. Add to this doc instead.
4. **Owner requests future feature?** → Move from here to scope doc with new phase

## 16. Cross-References

- Product scope (what IS in Phase 1): `01_PRODUCT_SCOPE.md`
- Architecture (future-ready design): `02_SYSTEM_ARCHITECTURE.md`
- Dev phases: `16_DEVELOPMENT_PHASES.md`
