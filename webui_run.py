"""Run a single TradingAgents analysis, invoked by webui.py as a subprocess."""
import json
import sys

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

ticker, trade_date = sys.argv[1], sys.argv[2]

print(f"[run] ticker={ticker} date={trade_date}", flush=True)
config = DEFAULT_CONFIG.copy()
ta = TradingAgentsGraph(debug=True, config=config)
_, decision = ta.propagate(ticker, trade_date)

out = {"ticker": ticker, "date": trade_date, "decision": decision}
with open(f"reports/webui_decision_{ticker}.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print("[run] done, decision saved", flush=True)
