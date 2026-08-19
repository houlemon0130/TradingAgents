"""Measure SPY's current position and the historical outcome of entering at all-time highs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import net_bootstrap  # noqa: E402

print(f"[net] {net_bootstrap.apply()}", flush=True)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import yfinance as yf  # noqa: E402


def pct(x):
    return f"{x * 100:.1f}%"


spy = yf.download("SPY", start="1993-01-01", auto_adjust=True, progress=False)["Close"]
if isinstance(spy, pd.DataFrame):
    spy = spy.iloc[:, 0]
spy = spy.dropna()

gspc = yf.download("^GSPC", start="1950-01-01", auto_adjust=True, progress=False)["Close"]
if isinstance(gspc, pd.DataFrame):
    gspc = gspc.iloc[:, 0]
gspc = gspc.dropna()

print("\n=== 1. 当前位置（SPY 总回报口径, 含分红再投）===")
last = spy.iloc[-1]
print(f"最新收盘 {spy.index[-1].date()}: {last:.2f}")
w52 = spy.iloc[-252:]
print(f"52 周区间: {w52.min():.2f} – {w52.max():.2f}")
print(f"52 周区间位置: {pct((last - w52.min()) / (w52.max() - w52.min()))}")
ath = spy.max()
print(f"历史最高: {ath:.2f} ({spy.idxmax().date()}), 距高点: {pct(last / ath - 1)}")
ma50, ma200 = spy.iloc[-50:].mean(), spy.iloc[-200:].mean()
print(f"MA50 {ma50:.2f} (偏离 {pct(last / ma50 - 1)}), MA200 {ma200:.2f} (偏离 {pct(last / ma200 - 1)})")
delta = spy.diff()
gain = delta.clip(lower=0).rolling(14).mean().iloc[-1]
loss = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
print(f"RSI(14): {100 - 100 / (1 + gain / loss):.1f}")

print("\n=== 2. '在历史新高'有多罕见 ===")
for name, s in (("SPY 1993-", spy), ("S&P500 1950-", gspc)):
    running = s.cummax()
    at_ath = s >= running * 0.999
    near = s >= running * 0.98
    print(f"{name}: 处于新高(±0.1%)的交易日占 {pct(at_ath.mean())}; "
          f"距高点 2% 以内的交易日占 {pct(near.mean())}")

print("\n=== 3. 在新高买入 vs 任意一天买入：之后的年化回报 ===")
print("(SPY 总回报，1993-2026；重叠窗口，样本独立性有限)")
running = spy.cummax()
at_ath = (spy >= running * 0.999).values
vals = spy.values
idx = spy.index
for years in (1, 3, 5, 10):
    h = int(years * 252)
    if h >= len(vals):
        continue
    fwd = vals[h:] / vals[:-h]
    ann = fwd ** (1 / years) - 1
    mask = at_ath[:-h]
    rows = []
    for label, sample in (("新高买入", ann[mask]), ("任意一天", ann)):
        rows.append(f"{label}: n={len(sample):5d} 中位 {pct(np.median(sample)):>7} "
                    f"最差 {pct(sample.min()):>7} 亏损概率 {pct((sample < 0).mean()):>6}")
    print(f"\n持有 {years:2d} 年 → " + "\n              ".join(rows))

print("\n=== 4. 在新高买入后的最大回撤（未来 12 个月内）===")
h = 252
dd = []
for i in np.where(at_ath[:-h])[0]:
    window = vals[i:i + h]
    dd.append(window.min() / vals[i] - 1)
dd = np.array(dd)
print(f"n={len(dd)}  中位 {pct(np.median(dd))}  最差 {pct(dd.min())}")
for thr in (-0.05, -0.10, -0.20, -0.30):
    print(f"  未来 12 个月内一度跌破 {pct(thr)}: {pct((dd <= thr).mean())} 的新高买入日")

print("\n=== 5. 最坏情况：在历史大顶买入，要多久回本（SPY 总回报）===")
for peak in ("2000-03-24", "2007-10-09", "2020-02-19", "2021-12-27", "2022-01-03"):
    try:
        p0 = spy.loc[:peak].iloc[-1]
        after = spy.loc[peak:]
        rec = after[after >= p0]
        trough = after.min() / p0 - 1
        if len(rec) > 1:
            days = (rec.index[1] - after.index[0]).days
            print(f"{peak} 买入: 最深 {pct(trough)}, {days} 天({days/365:.1f}年)回本")
        else:
            print(f"{peak} 买入: 最深 {pct(trough)}, 尚未回本")
    except Exception as e:  # noqa: BLE001
        print(f"{peak}: {e}")

print("\n=== 6. 估值与宏观（当前）===")
t = yf.Ticker("SPY")
try:
    info = t.get_info()
    for k in ("trailingPE", "forwardPE", "yield", "navPrice"):
        if info.get(k) is not None:
            print(f"SPY {k}: {info[k]}")
except Exception as e:  # noqa: BLE001
    print(f"SPY info failed: {e}")
for sym, label in (("^VIX", "VIX 波动率指数"), ("^TNX", "美债 10 年收益率(×10)")):
    try:
        s = yf.download(sym, period="1mo", auto_adjust=True, progress=False)["Close"]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        print(f"{label}: 最新 {s.dropna().iloc[-1]:.2f}")
    except Exception as e:  # noqa: BLE001
        print(f"{label} failed: {e}")

print("\n=== 7. 分批 12 个月 vs 一次性：从新高起投的历史对比 ===")
print("(每月 1 笔 × 12 个月 vs 第 0 天一次性全投，比较 3 年后的资产)")
month_ends = spy.resample("ME").last()
me_idx = list(month_ends.index)
lump_wins = 0
total = 0
diffs = []
for i in range(len(me_idx) - 48):
    d0 = me_idx[i]
    hist = spy.loc[:d0]
    if hist.iloc[-1] < hist.max() * 0.999:
        continue
    end = me_idx[i + 36]
    p_end = month_ends.loc[end]
    lump = p_end / month_ends.iloc[i]
    dca = np.mean([p_end / month_ends.iloc[i + k] for k in range(12)])
    total += 1
    lump_wins += lump > dca
    diffs.append(lump - dca)
if total:
    print(f"从'新高月末'起算的样本 n={total}: 一次性跑赢 12 个月分批的比例 "
          f"{pct(lump_wins / total)}, 3 年后资产差异中位 {pct(float(np.median(diffs)))}")
