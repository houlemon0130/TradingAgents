#!/usr/bin/env python3
"""TradingAgents 数据源健康检查。

在跑任何深度分析前先跑本脚本；全部 OK 才能继续。脚本本身也会被
data_source_guards 修复逻辑保护，FAIL 时按输出里的说明处理（见
references/data-sources.md）。

Usage:
    .venv/bin/python .qoder/skills/tradingagents-analyze/scripts/check_data_sources.py [TICKER]

Exit code: 0 = 全部数据源可用；1 = 至少一个 FAIL，禁止开始分析。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]          # cvs 仓库根
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

import net_bootstrap  # noqa: E402 — 必须先于任何联网 import

net_bootstrap.apply()

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402 — 触发 .env 加载

import data_source_guards  # noqa: E402

TICKER = sys.argv[1] if len(sys.argv) > 1 else "CVS"

# 每个数据源用"成功特征"判定（数值数据会误匹配 403/429/error 等失败词，
# 例如成交量 12403400 含子串 "403"）。
SUCCESS_MARKERS = {
    "stocktwits": lambda t: ("Bullish" in t or "Total:" in t)
    and "unavailable" not in t.lower(),
    "reddit": lambda t: t.startswith("Reddit search for")
    or t.startswith("<no Reddit posts")
    or t.startswith("<no posts found"),
    "polymarket": lambda t: t.startswith("## Polymarket prediction markets")
    or t.startswith("## Prediction markets for"),
    "fred": lambda t: t.startswith("## FRED"),
    "yahoo_ohlcv": lambda t: t.startswith("# Stock data for") and "Total records:" in t,
    "yahoo_news": lambda t: t.startswith("## ") and "News" in t,
    "fundamentals": lambda t: t.startswith("# Company Fundamentals"),
    "llm_dashscope": lambda t: "ok" in t.lower(),
}

results = []


def check(name, fn):
    try:
        text = str(fn())
    except Exception as exc:
        results.append((False, name, f"{type(exc).__name__}: {exc}"))
        return
    marker = SUCCESS_MARKERS.get(name)
    if marker is None:
        results.append((False, name, "no success marker defined"))
    elif marker(text):
        results.append((True, name, text.strip().replace("\n", " ")[:120]))
    else:
        results.append((False, name, text.strip().replace("\n", " ")[:200]))


def main():
    guard = data_source_guards.install()
    print(f"[backends] {guard}", flush=True)

    from tradingagents.dataflows import stocktwits, reddit, polymarket
    from tradingagents.agents.utils.agent_utils import (
        get_stock_data, get_news, get_macro_indicators, get_fundamentals,
    )

    check("stocktwits", lambda: stocktwits.fetch_stocktwits_messages(TICKER))
    check("reddit", lambda: reddit.fetch_reddit_posts(TICKER))
    check("polymarket", lambda: polymarket.get_prediction_markets("Fed rate cut", 3))
    check("fred", lambda: get_macro_indicators.invoke(
        {"indicator": "cpi", "curr_date": "2026-08-18", "look_back_days": 90}))
    check("yahoo_ohlcv", lambda: get_stock_data.invoke(
        {"symbol": TICKER, "start_date": "2026-08-01", "end_date": "2026-08-18"}))
    check("yahoo_news", lambda: get_news.invoke(
        {"ticker": TICKER, "start_date": "2026-08-11", "end_date": "2026-08-18"}))
    check("fundamentals", lambda: get_fundamentals.invoke(
        {"ticker": TICKER, "curr_date": "2026-08-18"}))

    # LLM 端点（DashScope/百炼）连通性：用一个极短请求探测，不调完整模型。
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://dashscope.aliyuncs.com/compatible-mode/v1/models",
            headers={"Authorization": f"Bearer {__import__('os').environ.get('DASHSCOPE_CN_API_KEY', '')}"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
        results.append((ok, "llm_dashscope",
                        "models endpoint ok" if ok else f"HTTP {resp.status}"))
    except Exception as exc:
        results.append((False, "llm_dashscope", f"{type(exc).__name__}: {exc}"))

    print(flush=True)
    failed = 0
    for ok, name, detail in results:
        status = "OK" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"[{status}] {name}: {detail}", flush=True)

    print(flush=True)
    if failed:
        print(f"GATE: {failed} data source(s) unhealthy — DO NOT run analysis. "
              "Fix per references/data-sources.md, then re-run this check.", flush=True)
        sys.exit(1)
    print(f"GATE: all data sources healthy — analysis may proceed (ticker {TICKER}).", flush=True)


if __name__ == "__main__":
    main()
