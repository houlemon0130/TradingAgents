"""每日持仓/观察名单检查：报收盘价、盘中最低、以及触发状态。

为什么要单独看"盘中最低"：币安股票没有止损单，用户在美股时段（北京 21:30–04:00）
基本是睡着的。报告给的触发条件都是**收盘价**口径，但如果盘中曾深跌又拉回，
收盘规则会忽略它——这个信息必须显式报出来，否则用户以为"没事发生"。

持仓有两个来源：下面的 POSITIONS（人工模式，原样保留可用），或 --stdin 从
标准输入读 JSON（dsh 的 ta_watch_position 工具走这条——持仓由 ta_extract_levels
从报告散文里抽出来，不该再硬编码在这里）。选 stdin 而不是临时文件：持仓含成本价
与股数，不落盘就不会在 /tmp 残留；dsh 的 subprocess 也原生支持"写完即关"的 stdin，
没有临时文件的创建/清理/进程崩了留垃圾的问题。

Usage:
    .venv/bin/python scripts/watch_position.py                       # 用下面的 POSITIONS
    echo '{"positions": {...}}' | .venv/bin/python scripts/watch_position.py --stdin
    ... | .venv/bin/python scripts/watch_position.py --stdin --json   # stdout 只吐一个 JSON

--json 模式下 stdout **只有**一个 JSON 对象（给程序读）；人可读的 markdown 仍然
照常追加到 reports/watch_log.md。
"""
import json
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


def load_positions():
    """从 stdin 读持仓。接受 {"positions": {...}}，也接受直接的 {ticker: plan} 映射。"""
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("--stdin 模式下 stdin 是空的，期望一个 JSON 对象")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"stdin 不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("stdin 顶层必须是 JSON 对象")
    positions = payload.get("positions", payload)
    if not isinstance(positions, dict) or not positions:
        raise SystemExit("positions 必须是非空的 {ticker: plan} 映射")
    for ticker, plan in positions.items():
        if not isinstance(plan, dict):
            raise SystemExit(f"{ticker} 的持仓计划必须是对象")
    return positions


def check(ticker, cfg):
    """检查一个标的。

    返回 (markdown 行, 需要动手的 alert 文案, 结构化记录) 三元组。
    业务逻辑与硬编码版本逐条等价——尤其是「收盘价触发」与「盘中曾破但收回」
    分开报这一条：前者进 trigger（可执行），后者只进叙述。

    入参可能来自 LLM 抽取，所以每个字段都用 .get() 且容忍 None：
    抽不到就是 null，不是 0（`cfg["shares"]` 那种直接下标会 KeyError）。
    """
    blank = {"ticker": ticker, "date": None, "close": None, "change_pct": None,
             "intraday_low": None, "intraday_high": None, "trigger": None}
    try:
        hist = yf.Ticker(ticker).history(period="5d")
    except Exception as exc:
        message = f"取数失败 {type(exc).__name__}"
        return [f"- **{ticker}**: {message}"], [], {**blank, "message": message}
    if hist.empty:
        return [f"- **{ticker}**: 无数据"], [], {**blank, "message": "无数据"}

    close = float(hist["Close"].iloc[-1])
    low = float(hist["Low"].iloc[-1])
    high = float(hist["High"].iloc[-1])
    prev = float(hist["Close"].iloc[-2]) if len(hist) > 1 else close
    day = hist.index[-1].date()
    chg = (close / prev - 1) * 100

    lines, alerts, notes = [], [], []
    shares = cfg.get("shares") or 0
    cost = cfg.get("cost")
    plan_note = cfg.get("note") or ""

    head = f"- **{ticker}** {day} 收 {close:.2f}（{chg:+.2f}%），盘中 {low:.2f}–{high:.2f}"
    if shares and cost is not None:
        pnl = (close - cost) * shares
        head += f" · 持仓 {shares} 股 @{cost}，浮动 {pnl:+.2f}"
    elif shares:
        head += f" · 持仓 {shares} 股（成本未知）"
    lines.append(head)

    trigger = None
    reduce_at, exit_at = cfg.get("reduce"), cfg.get("exit")
    # 收盘口径：真正的执行触发
    if exit_at and close < exit_at:
        trigger = "exit"
        alerts.append(f"{ticker} 收盘 {close:.2f} 跌破清仓线 {exit_at} → {plan_note}")
    elif reduce_at and close < reduce_at:
        trigger = "reduce"
        alerts.append(f"{ticker} 收盘 {close:.2f} 跌破减仓线 {reduce_at} → {plan_note}")
    # 盘中口径：收盘没破但盘中破过，属于"你睡觉时发生过的事"
    elif reduce_at and low < reduce_at:
        notes.append(f"盘中曾跌破 {reduce_at}（最低 {low:.2f}）但收回，收盘规则未触发")
        lines.append(f"    {notes[-1]}")

    for tgt in cfg.get("targets") or []:
        if close >= tgt:
            alerts.append(f"{ticker} 收盘 {close:.2f} 达到目标 {tgt} → 按计划减仓")
            break

    zone = cfg.get("buy_zone")
    if not shares and zone and zone[0] <= close <= zone[1]:
        if trigger is None:
            trigger = "in_buy_zone"
        notes.append(f"现价在建仓区 {zone[0]}–{zone[1]} 内")
        lines.append(f"    {notes[-1]}")

    record = {
        "ticker": ticker,
        "date": str(day),
        "close": round(close, 4),
        "change_pct": round(chg, 4),
        "intraday_low": round(low, 4),
        "intraday_high": round(high, 4),
        "trigger": trigger,
        # message 自带完整叙述：收盘触发同时进 trigger，盘中插针与目标到位只在这里。
        "message": " · ".join([head.lstrip("- ").replace("**", "")] + alerts + notes),
    }
    return lines, alerts, record


def main():
    as_of = f"{datetime.now():%Y-%m-%d %H:%M}"
    want_json = "--json" in sys.argv
    positions = load_positions() if "--stdin" in sys.argv else POSITIONS

    lines = [f"\n## {as_of} 检查\n"]
    alerts, records = [], []

    for ticker, cfg in positions.items():
        ticker_lines, ticker_alerts, record = check(ticker, cfg)
        lines += ticker_lines
        alerts += ticker_alerts
        records.append(record)

    if alerts:
        lines.append("\n### ⚠️ 需要动手\n")
        lines += [f"- {a}" for a in alerts]
    else:
        lines.append("\n无触发，按计划持有。")

    text = "\n".join(lines)
    # 人可读日志两种模式都照常落盘；--json 下 stdout 让给协议。
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a") as f:
        f.write(text + "\n")

    if want_json:
        print(json.dumps({
            "as_of": as_of,
            "appended_to": str(LOG.resolve()),
            "alerts": records,
        }, ensure_ascii=False), flush=True)
    else:
        print(text)


if __name__ == "__main__":
    main()
