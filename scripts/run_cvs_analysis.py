"""运行完整 TradingAgents 管线分析 CVS（注入用户持仓上下文）"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))  # 让 net_bootstrap / data_source_guards 可导入
sys.path.insert(0, str(ROOT))

import net_bootstrap  # noqa: E402 — 必须先于任何联网 import 执行

net_bootstrap.apply()

from tradingagents.default_config import DEFAULT_CONFIG  # noqa: E402
from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: E402

import data_source_guards  # noqa: E402

print(f"[data] {data_source_guards.install()}", flush=True)

config = DEFAULT_CONFIG.copy()

user_context = (
    "当前持仓: 3 股 @ $94.50\n"
    "减仓计划: 跌破 $90 减 2 股\n"
    "清仓计划: 放量收盘破 $84.23 清仓\n"
    "目标价: $105 和 $110\n"
    "当前浮动盈亏: +$1.23 (盈亏平衡)"
)

ta = TradingAgentsGraph(debug=True, config=config)
final_state, decision = ta.propagate("CVS", "2026-08-18", user_context=user_context)

# 存报告
report_path = ta.save_reports(final_state, "CVS")
print(f"\n{'='*60}")
print(f"报告已保存: {report_path}")
print(f"{'='*60}")
print(f"\n交易决策信号: {decision}")
print(f"{'='*60}")

# 打印 PM 最终决策
print(f"\n📋 Portfolio Manager 最终决策:\n{final_state.get('final_trade_decision', '(空)')}")