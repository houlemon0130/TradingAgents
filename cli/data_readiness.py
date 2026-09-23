"""Fail closed when inputs required by the selected analysts are unavailable."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from io import StringIO
from zoneinfo import ZoneInfo

import pandas as pd

from tradingagents.dataflows.config import set_config
from tradingagents.dataflows.interface import route_to_vendor
from tradingagents.dataflows.stockstats_utils import load_ohlcv


class DataNotReadyError(RuntimeError):
    """The analysis must not start with an incomplete primary data snapshot."""


def _require_source(name: str, fetch) -> str:
    try:
        value = fetch()
    except Exception as exc:
        raise DataNotReadyError(f"{name}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(value, str) or not value.strip():
        raise DataNotReadyError(f"{name}: empty response")
    if value.startswith(("NO_DATA_AVAILABLE:", "DATA_UNAVAILABLE:", "DATA_WITHHELD:")):
        raise DataNotReadyError(f"{name}: {value.splitlines()[0]}")
    if value.startswith(("<Reddit unavailable:", "<Fear & Greed unavailable:", "<stocktwits unavailable:")):
        raise DataNotReadyError(f"{name}: {value.splitlines()[0]}")
    if value.startswith("Polymarket data is currently unavailable"):
        raise DataNotReadyError(f"{name}: {value.splitlines()[0]}")
    return value


def _last_us_completed_weekday(analysis_date: str) -> date:
    """Conservative US session cutoff; an exchange holiday blocks rather than guessing."""
    target = date.fromisoformat(analysis_date)
    now = datetime.now(ZoneInfo("America/New_York"))
    if target >= now.date() and now.time() < time(17):
        target = min(target, now.date() - timedelta(days=1))
    while target.weekday() >= 5:
        target -= timedelta(days=1)
    return target


def _require_complete_price(ticker: str, analysis_date: str, asset_type: str = "stock") -> None:
    start = (date.fromisoformat(analysis_date) - timedelta(days=14)).isoformat()
    raw = _require_source(
        "OHLCV", lambda: route_to_vendor("get_stock_data", ticker, start, analysis_date)
    )
    csv_start = raw.find("Date,Open,High,Low,Close,Volume")
    if csv_start < 0:
        raise DataNotReadyError("OHLCV: vendor did not return a dated price table")
    rows = pd.read_csv(StringIO(raw[csv_start:]))
    rows["Date"] = pd.to_datetime(rows["Date"], errors="coerce")
    rows = rows[rows["Date"] <= pd.Timestamp(analysis_date)].sort_values("Date")
    if rows.empty:
        raise DataNotReadyError(f"OHLCV: no price row through {analysis_date}")
    latest = rows.iloc[-1]
    # A missing entire US session row must also block. Symbols with exchange
    # suffixes use other calendars; don't assume a US close for those markets.
    if asset_type == "stock" and "." not in ticker and "=" not in ticker:
        expected = _last_us_completed_weekday(analysis_date)
        if latest["Date"].date() != expected:
            raise DataNotReadyError(
                f"OHLCV: latest row is {latest['Date'].date()}, "
                f"but the latest expected completed US session is {expected}"
            )
    if asset_type == "crypto":
        expected = min(date.fromisoformat(analysis_date), datetime.now(timezone.utc).date() - timedelta(days=1))
        if latest["Date"].date() != expected:
            raise DataNotReadyError(
                f"OHLCV: latest row is {latest['Date'].date()}, "
                f"but the latest completed UTC daily candle is {expected}"
            )
    missing = [name for name in ("Open", "High", "Low", "Close", "Volume")
               if name not in rows or pd.isna(latest[name])]
    if missing:
        raise DataNotReadyError(
            f"OHLCV: {latest['Date'].date()} has no {', '.join(missing)}; "
            "wait for a complete vendor bar before analysis"
        )

    # Indicators use a separate cached download. Both paths must agree on the
    # latest usable date; otherwise the model would mix different price vintages.
    try:
        indicators = load_ohlcv(ticker, analysis_date, fill_gaps=False)
    except Exception as exc:
        raise DataNotReadyError(f"indicators: {type(exc).__name__}: {exc}") from exc
    if indicators.empty or pd.Timestamp(indicators.iloc[-1]["Date"]).date() != latest["Date"].date():
        raise DataNotReadyError("indicators: latest price date differs from OHLCV source")


def check_data_readiness(selections: dict, config: dict) -> None:
    """Probe selected analysts' primary inputs before building the LLM graph."""
    set_config(config)
    ticker = selections["ticker"]
    analysis_date = selections["analysis_date"]
    analysts = {getattr(a, "value", a) for a in selections["analysts"]}
    asset_type = selections["asset_type"]
    start = (date.fromisoformat(analysis_date) - timedelta(days=7)).isoformat()

    # The final decision uses market prices even when Market is deselected.
    _require_complete_price(ticker, analysis_date, asset_type)

    if "fundamentals" in analysts and asset_type == "stock":
        for method in ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"):
            _require_source(method, lambda method=method: route_to_vendor(method, ticker, analysis_date))

    if "news" in analysts or "social" in analysts:
        _require_source("ticker news", lambda: route_to_vendor("get_news", ticker, start, analysis_date))

    if "news" in analysts:
        _require_source("global news", lambda: route_to_vendor("get_global_news", analysis_date, 7, 5))
        _require_source("FRED macro", lambda: route_to_vendor("get_macro_indicators", "fed_funds_rate", analysis_date, 30))
        _require_source("prediction markets", lambda: route_to_vendor("get_prediction_markets", "Fed rate", 5, analysis_date))
        if asset_type == "stock":
            _require_source("insider filings", lambda: route_to_vendor("get_insider_transactions", ticker, analysis_date))

    if "social" in analysts:
        from tradingagents.agents.analysts import sentiment_analyst

        _require_source("Reddit", lambda: sentiment_analyst.fetch_reddit_posts(
            ticker, start_date=start, end_date=analysis_date
        ))
        # Probe only sources the active analyst actually uses.
        if hasattr(sentiment_analyst, "fetch_stocktwits_messages"):
            _require_source("StockTwits", lambda: sentiment_analyst.fetch_stocktwits_messages(
                ticker, limit=30, start_date=start, end_date=analysis_date
            ))
        if asset_type == "crypto" and hasattr(sentiment_analyst, "fetch_fear_greed"):
            _require_source("BTC Fear & Greed", lambda: sentiment_analyst.fetch_fear_greed(
                start, analysis_date
            ))
