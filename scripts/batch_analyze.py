"""Batch-deep-analyze a shortlist with TradingAgents, saving a summary per ticker.

Usage: .venv/bin/python scripts/batch_analyze.py [YYYY-MM-DD] HIG CVS GM EME GPN
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import net_bootstrap  # noqa: E402  — must run before any networked import

print(f"[net] {net_bootstrap.apply()}", flush=True)

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

import data_source_guards  # noqa: E402

print(f"[data] {data_source_guards.install()}", flush=True)

args = sys.argv[1:]
DATE = args.pop(0) if args and re.fullmatch(r"\d{4}-\d{2}-\d{2}", args[0]) else "2026-08-12"
TICKERS = args or ["HIG", "CVS", "GM", "EME", "GPN"]
OUT_DIR = Path("reports") / f"batch_{datetime.now():%Y%m%d_%H%M%S}"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    config = DEFAULT_CONFIG.copy()
    for i, ticker in enumerate(TICKERS, 1):
        print(f"\n=== [{i}/{len(TICKERS)}] {ticker} @ {DATE} ===", flush=True)
        try:
            ta = TradingAgentsGraph(debug=True, config=config)
            final_state, decision = ta.propagate(ticker, DATE)
            summary = {
                "ticker": ticker,
                "date": DATE,
                "final_decision": decision,
                "final_trade_decision": final_state.get("final_trade_decision"),
                "trader_plan": final_state.get("trader_investment_plan"),
                "research_manager": (final_state.get("investment_debate_state") or {}).get(
                    "judge_decision"),
                "risk_manager": (final_state.get("risk_debate_state") or {}).get(
                    "judge_decision"),
                "market_report": final_state.get("market_report"),
                "sentiment_report": final_state.get("sentiment_report"),
                "news_report": final_state.get("news_report"),
                "fundamentals_report": final_state.get("fundamentals_report"),
            }
            with open(OUT_DIR / f"{ticker}.json", "w") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
            print(f"[{ticker}] FINAL: {decision}", flush=True)
        except Exception as e:
            print(f"[{ticker}] FAILED: {e}", flush=True)
            with open(OUT_DIR / f"{ticker}.json", "w") as f:
                json.dump({"ticker": ticker, "date": DATE, "error": str(e)},
                          f, ensure_ascii=False, indent=2)
    print(f"\nsummary dir: {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
