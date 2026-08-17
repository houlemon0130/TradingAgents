"""每日持仓/观察名单检查：报收盘价、盘中最低、以及触发状态。

为什么要单独看"盘中最低"：币安股票没有止损单，用户在美股时段（北京 21:30–04:00）
基本是睡着的。报告给的触发条件都是**收盘价**口径，但如果盘中曾深跌又拉回，
收盘规则会忽略它——这个信息必须显式报出来，否则用户以为"没事发生"。

Usage: .venv/bin/python scripts/watch_position.py
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import net_bootstrap  # noqa: E402

net_bootstrap.apply()

import yfinance as yf  # noqa: E402

LOG = Path("reports/watch_log.md")

# 持仓：股数 + 成本；观察：股数 0
POSITIONS = {
    "CVS":  {"shares": 3, "cost": 94.50,
             "reduce": 90.00, "exit": 84.23, "targets": [105, 110],
             "note": "跌破 90 减仓 2 股；放量收盘破 200 日线 84.23 清仓"},
    "SCHW": {"shares": 0, "cost": None,
             "buy_zone": (105, 107), "buy_zone2": (98.7, 100),
             "reduce": 98.70, "exit": 95.80, "targets": [117],
             "note": "回踩 105-107 建首批；破 98.7 减半，95.8 离场"},
    "ELV":  {"shares": 0, "cost": None,
             "buy_zone": (392, 404), "reduce": 392, "exit": 350.85,
             "targets": [445, 475],
             "note": "首批现价附近 1/3；收盘破 10 日线 392 走；放量突破 404.72 加"},
    "CCL":  {"shares": 0, "cost": None,
             "buy_zone": (27.5, 28.5), "reduce": 26.40, "exit": 26.40,
             "targets": [31, 34],
             "note": "27.5-28.5 分批；收盘破 26.4 无条件止损"},
}


def main():
    lines = [f"\n## {datetime.now():%Y-%m-%d %H:%M} 检查\n"]
    alerts = []

    for ticker, cfg in POSITIONS.items():
        try:
            hist = yf.Ticker(ticker).history(period="5d")
        except Exception as exc:
            lines.append(f"- **{ticker}**: 取数失败 {type(exc).__name__}")
            continue
        if hist.empty:
            lines.append(f"- **{ticker}**: 无数据")
            continue

        close = float(hist["Close"].iloc[-1])
        low = float(hist["Low"].iloc[-1])
        high = float(hist["High"].iloc[-1])
        prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
        day = hist.index[-1].date()
        chg = (close / prev - 1) * 100

        head = f"- **{ticker}** {day} 收 {close:.2f}（{chg:+.2f}%），盘中 {low:.2f}–{high:.2f}"
        if cfg["shares"]:
            pnl = (close - cfg["cost"]) * cfg["shares"]
            head += f" · 持仓 {cfg['shares']} 股 @{cfg['cost']}，浮动 {pnl:+.2f}"
        lines.append(head)

        reduce_at, exit_at = cfg.get("reduce"), cfg.get("exit")
        # 收盘口径：真正的执行触发
        if exit_at and close < exit_at:
            alerts.append(f"{ticker} 收盘 {close:.2f} 跌破清仓线 {exit_at} → {cfg['note']}")
        elif reduce_at and close < reduce_at:
            alerts.append(f"{ticker} 收盘 {close:.2f} 跌破减仓线 {reduce_at} → {cfg['note']}")
        # 盘中口径：收盘没破但盘中破过，属于"你睡觉时发生过的事"
        elif reduce_at and low < reduce_at:
            lines.append(f"    盘中曾跌破 {reduce_at}（最低 {low:.2f}）但收回，收盘规则未触发")

        for tgt in cfg.get("targets", []):
            if close >= tgt:
                alerts.append(f"{ticker} 收盘 {close:.2f} 达到目标 {tgt} → 按计划减仓")
                break

        zone = cfg.get("buy_zone")
        if not cfg["shares"] and zone and zone[0] <= close <= zone[1]:
            lines.append(f"    现价在建仓区 {zone[0]}–{zone[1]} 内")

    if alerts:
        lines.append("\n### ⚠️ 需要动手\n")
        lines += [f"- {a}" for a in alerts]
    else:
        lines.append("\n无触发，按计划持有。")

    text = "\n".join(lines)
    print(text)
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(text + "\n")


if __name__ == "__main__":
    main()
