"""Read-only market intelligence for QUANTARA Options, borrowed from System A skills.

Sources (all optional, all time-boxed, all failing soft):
  - Yahoo Finance headline RSS per ticker, parsed with System A's
    `QUANTARA-AI-Trader/trader-news/news.py` (`parse_rss` + `tag`) when that file exists,
    otherwise with a built-in minimal parser.
  - Keyword sentiment, earnings/event flags and macro flags derived from those headlines
    (the same keyword taxonomy System A's news skill uses).
  - System A's `trader/ai-status.json` (read-only) for shared Groq token usage awareness.

Nothing here ever raises to the caller: offline, market closed, or missing System A files
all produce a valid (possibly empty) context with `sources_ok=False` and notes.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

log = logging.getLogger("quantara_options.skills_context")

YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%s&region=US&lang=en-US"
BROWSER_UA = "Mozilla/5.0 (compatible; quantara-options/0.1)"

_FALLBACK_KEYWORDS = [
    ("earnings", r"\bearnings\b|\bresults\b|\bEPS\b|\bguidance\b|\bforecast\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|price target|\boutperform\b|\bunderperform\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bSEC charges\b|\bantitrust\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bbuyout\b|\btakeover\b"),
    ("risk", r"\brecall\b|\bbankrupt|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)"),
    ("regulatory", r"\bFDA\b|\bapproval\b|\bFTC\b|\bDOJ\b"),
    ("macro", r"\bFed\b|\bFOMC\b|\bCPI\b|\binflation\b|\bjobs report\b|\bpayrolls\b|\btariffs?\b"),
]

_POSITIVE = re.compile(
    r"\b(beats?|surges?|soars?|jumps?|rall(?:y|ies)|record high|upgrade[sd]?|outperform|raises? guidance|"
    r"strong demand|tops estimates|buyback|bullish|gains?)\b", re.I)
_NEGATIVE = re.compile(
    r"\b(miss(?:es|ed)?|plunges?|tumbles?|sinks?|slumps?|falls?|drops?|downgrade[sd]?|underperform|"
    r"cuts? guidance|lawsuit|probe|investigation|recall|bankrupt\w*|default|halt(?:ed)?|bearish|warns?|"
    r"layoffs?|sell-?off|crash)\b", re.I)
_EARNINGS_SOON = re.compile(
    r"\b(to report|reports? (?:on|after|before)|earnings (?:preview|date|call|ahead|this week|next week)|"
    r"ahead of (?:its )?earnings|earnings on deck|what to expect)\b", re.I)


def _default_system_a_root() -> Path:
    env_root = os.environ.get("QUANTARA_AI_TRADER_ROOT")
    if env_root:
        return Path(env_root)
    # apps/engine/quantara_options/ai/skills_context.py -> FINAL-WEB/QUANTARA-AI-Trader
    return Path(__file__).resolve().parents[5] / "QUANTARA-AI-Trader"


@dataclass
class SymbolIntel:
    symbol: str
    news_score: Optional[float] = None  # [-1, 1]
    news_count: int = 0
    headlines: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    earnings_flag: bool = False
    risk_flags: list[str] = field(default_factory=list)
    ok: bool = False
    error: Optional[str] = None

    def compact(self) -> dict[str, Any]:
        out: dict[str, Any] = {"news": self.news_score, "n": self.news_count}
        if self.earnings_flag:
            out["earnings"] = True
        if self.risk_flags:
            out["risk"] = self.risk_flags[:3]
        if self.headlines:
            out["top"] = [h[:90] for h in self.headlines[:2]]
        return out

    def as_evidence(self) -> dict[str, Any]:
        return {"news_score": self.news_score, "news_count": self.news_count,
                "earnings_flag": self.earnings_flag, "risk_flags": self.risk_flags,
                "tags": self.tags[:6], "headlines": self.headlines[:3], "ok": self.ok}


@dataclass
class SkillsContext:
    as_of: datetime
    symbols: dict[str, SymbolIntel] = field(default_factory=dict)
    macro_sentiment: Optional[float] = None
    macro_flags: list[str] = field(default_factory=list)
    sources_ok: bool = False
    source: str = "none"
    notes: list[str] = field(default_factory=list)

    def bias_for(self, symbol: str) -> float:
        intel = self.symbols.get(symbol)
        score = intel.news_score if intel and intel.news_score is not None else 0.0
        macro = self.macro_sentiment or 0.0
        return max(-1.0, min(1.0, 0.7 * score + 0.3 * macro))

    def evidence_for(self, symbol: str) -> dict[str, Any]:
        intel = self.symbols.get(symbol)
        return intel.as_evidence() if intel else {}

    def compact_for_prompt(self, symbols: Optional[list[str]] = None) -> dict[str, Any]:
        keys = symbols if symbols is not None else list(self.symbols)
        return {
            "ok": self.sources_ok,
            "macro": self.macro_sentiment,
            "macro_flags": self.macro_flags[:3],
            "sym": {s: self.symbols[s].compact() for s in keys if s in self.symbols and self.symbols[s].ok},
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "sources_ok": self.sources_ok,
            "source": self.source,
            "macro_sentiment": self.macro_sentiment,
            "macro_flags": self.macro_flags,
            "notes": self.notes,
            "symbols": {s: i.as_evidence() for s, i in self.symbols.items()},
        }


class SkillsContextProvider:
    def __init__(
        self,
        *,
        system_a_root: Optional[Path | str] = None,
        enabled: Optional[bool] = None,
        timeout_s: float = 4.0,
        total_budget_s: float = 10.0,
        cache_ttl_s: float = 900.0,
        hours: float = 48.0,
        max_workers: int = 4,
    ) -> None:
        self.root = Path(system_a_root) if system_a_root else _default_system_a_root()
        offline_env = os.environ.get("QUANTARA_OPTIONS_SKILLS_OFFLINE", "").strip().lower() in {"1", "true", "yes"}
        self.enabled = (not offline_env) if enabled is None else enabled
        self.timeout_s = timeout_s
        self.total_budget_s = total_budget_s
        self.cache_ttl_s = cache_ttl_s
        self.hours = hours
        self.max_workers = max_workers
        self._news_mod: Optional[ModuleType] = None
        self._news_mod_loaded = False
        self._cache: dict[str, tuple[float, SymbolIntel]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ System A bridges

    def _news_module(self) -> Optional[ModuleType]:
        if self._news_mod_loaded:
            return self._news_mod
        self._news_mod_loaded = True
        path = self.root / "trader-news" / "news.py"
        if not path.is_file():
            return None
        try:
            spec = importlib.util.spec_from_file_location("_quantara_system_a_news", path)
            if spec is None or spec.loader is None:
                return None
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "parse_rss") and hasattr(mod, "tag"):
                self._news_mod = mod
        except Exception as exc:
            log.info("System A news module unavailable: %s", type(exc).__name__)
            self._news_mod = None
        return self._news_mod

    def system_a_ai_status(self) -> dict[str, Any]:
        """Read-only view of System A's last Groq call (no secrets in that file)."""
        path = self.root / "trader" / "ai-status.json"
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return {}
        st = doc.get("ai_status") or {}
        last = st.get("last_call") or {}
        return {
            "last_ok_utc": st.get("last_ok_utc"),
            "tokens_in": last.get("tokens_in"),
            "tokens_out": last.get("tokens_out"),
            "provider": st.get("provider"),
            "model": st.get("model"),
        }

    # ------------------------------------------------------------ fetch / parse

    def _fetch(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "identity"})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return resp.read()

    def _parse(self, data: bytes, symbol: str) -> list[dict[str, Any]]:
        mod = self._news_module()
        if mod is not None:
            try:
                items = mod.parse_rss(data, symbol)
                return mod.tag(items, {symbol: None})
            except Exception:
                pass
        items: list[dict[str, Any]] = []
        root = ET.fromstring(data)
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            summary = (it.findtext("description") or "").strip()
            when = None
            try:
                when = parsedate_to_datetime((it.findtext("pubDate") or "").strip())
                if when and when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError, IndexError):
                when = None
            hay = f"{title} {summary}"
            tags = [label for label, rx in _FALLBACK_KEYWORDS if re.search(rx, hay, re.I)]
            items.append({"title": title, "summary": summary, "time": when, "tags": tags,
                          "ticker": symbol, "kind": "headline"})
        return items

    def _score(self, symbol: str, items: list[dict[str, Any]], now: datetime) -> SymbolIntel:
        cutoff = now - timedelta(hours=self.hours)
        recent = [i for i in items if i.get("time") is None or i["time"] >= cutoff]
        intel = SymbolIntel(symbol=symbol, ok=True, news_count=len(recent))
        if not recent:
            intel.news_score = 0.0
            return intel
        total = 0.0
        tags: set[str] = set()
        for it in recent:
            hay = f"{it.get('title', '')} {it.get('summary', '')}"
            pos = len(_POSITIVE.findall(hay))
            neg = len(_NEGATIVE.findall(hay))
            if pos or neg:
                total += (pos - neg) / float(pos + neg)
            tags.update(it.get("tags") or [])
            if _EARNINGS_SOON.search(hay):
                intel.earnings_flag = True
        intel.news_score = round(max(-1.0, min(1.0, total / max(3.0, len(recent) * 0.5))), 3)
        intel.tags = sorted(tags)
        intel.risk_flags = [t for t in ("risk", "legal", "halt", "regulatory") if t in tags]
        if "earnings" in tags and any(_EARNINGS_SOON.search(f"{i.get('title', '')}") for i in recent):
            intel.earnings_flag = True
        recent.sort(key=lambda i: i.get("time") or now, reverse=True)
        intel.headlines = [str(i.get("title") or "")[:140] for i in recent[:3]]
        return intel

    def _symbol_intel(self, symbol: str, now: datetime) -> SymbolIntel:
        with self._lock:
            hit = self._cache.get(symbol)
        if hit and time.time() - hit[0] < self.cache_ttl_s:
            return hit[1]
        try:
            data = self._fetch(YAHOO_RSS % symbol.upper().replace(".", "-"))
            intel = self._score(symbol, self._parse(data, symbol), now)
        except (urllib.error.URLError, TimeoutError, OSError, ET.ParseError, ValueError) as exc:
            intel = SymbolIntel(symbol=symbol, ok=False, error=type(exc).__name__)
        except Exception as exc:
            intel = SymbolIntel(symbol=symbol, ok=False, error=type(exc).__name__)
        with self._lock:
            self._cache[symbol] = (time.time(), intel)
        return intel

    # ------------------------------------------------------------ public API

    def build(self, symbols: list[str], *, now: Optional[datetime] = None) -> SkillsContext:
        now = now or datetime.now(timezone.utc)
        ctx = SkillsContext(as_of=now)
        syms = sorted({s.upper() for s in symbols if s})
        if not self.enabled or not syms:
            ctx.notes.append("skills context disabled or no symbols")
            return ctx
        ctx.source = "system_a_news" if self._news_module() is not None else "builtin_rss"
        try:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers,
                                                         thread_name_prefix="opt-skills")
            futures = {pool.submit(self._symbol_intel, s, now): s for s in syms}
            done, pending = concurrent.futures.wait(futures, timeout=self.total_budget_s)
            for fut in done:
                sym = futures[fut]
                try:
                    ctx.symbols[sym] = fut.result()
                except Exception as exc:
                    ctx.symbols[sym] = SymbolIntel(symbol=sym, ok=False, error=type(exc).__name__)
            for fut in pending:
                sym = futures[fut]
                ctx.symbols[sym] = SymbolIntel(symbol=sym, ok=False, error="timeout")
                fut.cancel()
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception as exc:
            ctx.notes.append(f"skills fetch failed: {type(exc).__name__}")
            return ctx

        ok = [i for i in ctx.symbols.values() if i.ok]
        ctx.sources_ok = bool(ok)
        if not ok:
            ctx.notes.append("news unavailable (offline / blocked); rules continue without news")
        index_syms = [s for s in ("SPY", "QQQ", "IWM", "DIA") if s in ctx.symbols and ctx.symbols[s].ok]
        base = index_syms or [i.symbol for i in ok]
        scores = [ctx.symbols[s].news_score for s in base if ctx.symbols[s].news_score is not None]
        if scores:
            ctx.macro_sentiment = round(sum(scores) / len(scores), 3)
        flags: set[str] = set()
        for i in ok:
            if "macro" in i.tags:
                flags.add("macro_event_headlines")
            if i.symbol in index_syms and i.risk_flags:
                flags.update(i.risk_flags)
        ctx.macro_flags = sorted(flags)
        return ctx
