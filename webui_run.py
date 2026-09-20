"""Run a single TradingAgents analysis, invoked by webui.py as a subprocess."""
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent / "scripts"))
import net_bootstrap  # noqa: E402

print(f"[net] {net_bootstrap.apply()}", flush=True)

import data_source_guards  # noqa: E402

from cli.models import AnalystType  # noqa: E402
from cli.utils import (  # noqa: E402
    detect_asset_type,
    filter_analysts_for_asset_type,
    normalize_ticker_symbol,
)
from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

print(f"[data] {data_source_guards.install()}", flush=True)

raw_ticker, trade_date = sys.argv[1], sys.argv[2]
ticker = normalize_ticker_symbol(raw_ticker)
asset_type = detect_asset_type(ticker)
analysts = filter_analysts_for_asset_type(list(AnalystType), asset_type)

print(
    f"[run] ticker={ticker} date={trade_date} asset_type={asset_type.value}",
    flush=True,
)
config = DEFAULT_CONFIG.copy()
ta = TradingAgentsGraph(
    selected_analysts=[analyst.value for analyst in analysts],
    debug=True,
    config=config,
)
final_state, decision = ta.propagate(
    ticker,
    trade_date,
    asset_type=asset_type.value,
)

out = {
    "ticker": ticker,
    "date": trade_date,
    "asset_type": asset_type.value,
    "final_decision": decision,
    "final_trade_decision": final_state.get("final_trade_decision"),
    "trader_plan": final_state.get("trader_investment_plan"),
    "research_manager": (final_state.get("investment_debate_state") or {}).get(
        "judge_decision"
    ),
    "risk_manager": (final_state.get("risk_debate_state") or {}).get(
        "judge_decision"
    ),
}
with open(f"reports/webui_decision_{ticker}.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2, default=str)
print("[run] done, decision saved", flush=True)
