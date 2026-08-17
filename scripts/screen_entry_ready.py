"""Entry-ready screen: quality names that are ALREADY in a buy zone.

Every deep-analysis verdict so far ended in "don't chase, wait for a pullback",
because the layer-1 screens ranked on momentum/PEG and therefore surfaced names
sitting at 52-week highs. This screen inverts that: it starts from the
fundamentally-clean names in reports/sp500_screen.csv and keeps only those whose
*price position* is what the debate layer keeps asking for —

  trend intact      : above the 200-DMA (by >=2%, not hugging the cliff)
  not extended      : within +8% of the 50-DMA
  not overbought    : RSI 14 between 38 and 62
  pulled back       : 4-22% off the 52-week high
  momentum turning  : MACD histogram positive, or improving over 3 sessions

Usage: .venv/bin/python scripts/screen_entry_ready.py
"""
import pandas as pd
import yfinance as yf

FUND_CSV = "reports/sp500_screen.csv"
OUT_CSV = "reports/entry_ready.csv"


def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 - 100 / (1 + gain / loss)


def macd_hist(series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    return macd - macd.ewm(span=9, adjust=False).mean()


def main():
    fund = pd.read_csv(FUND_CSV)
    clean = fund[(fund.pe_ttm > 0) & (fund.pe_ttm < 60) & (fund.peg > 0) & (fund.peg < 3)
                 & (fund.revenue_growth > 0) & (fund.fcf_b > 0)
                 & (fund.market_cap_b >= 10)].copy()
    # Storage/airline/precious-metal cyclicals whose PEG is a boom artifact.
    clean = clean[~clean.ticker.isin({"MU", "STX", "WDC", "DAL", "UAL", "LUV", "NEM"})]
    symbols = clean.ticker.tolist()
    print(f"基本面干净的候选: {len(symbols)}", flush=True)

    px = yf.download(symbols, period="18mo", interval="1d",
                     auto_adjust=True, progress=False, group_by="column")
    close, volume = px["Close"], px["Volume"]

    rows = []
    for sym in symbols:
        if sym not in close or close[sym].dropna().shape[0] < 220:
            continue
        s = close[sym].dropna()
        v = volume[sym].dropna()
        price = s.iloc[-1]
        sma50, sma200 = s.rolling(50).mean().iloc[-1], s.rolling(200).mean().iloc[-1]
        ema10 = s.ewm(span=10, adjust=False).mean().iloc[-1]
        r = rsi(s).iloc[-1]
        h = macd_hist(s)
        hi52 = s.iloc[-252:].max()
        tr = (s.rolling(2).max() - s.rolling(2).min()).rolling(14).mean().iloc[-1]
        rows.append({
            "ticker": sym, "price": price,
            "vs_50d": price / sma50 - 1, "vs_200d": price / sma200 - 1,
            "vs_10ema": price / ema10 - 1,
            "rsi": r, "macdh": h.iloc[-1], "macdh_prev3": h.iloc[-4],
            "off_52w_high": price / hi52 - 1,
            "atr_pct": tr / price,
            "vol_dryup": v.iloc[-20:].mean() / v.iloc[-60:].mean() - 1,
            "sma50_rising": sma50 > s.rolling(50).mean().iloc[-11],
        })

    tech = pd.DataFrame(rows)
    df = clean.merge(tech, on="ticker", suffixes=("_f", ""))

    f = df[(df.vs_200d >= 0.02) & (df.vs_50d <= 0.08)
           & (df.rsi.between(38, 62))
           & (df.off_52w_high.between(-0.22, -0.04))
           & ((df.macdh > 0) | (df.macdh > df.macdh_prev3))]

    # Rank: near the 50-DMA, RSI mid-range, momentum improving, cheap-ish growth.
    f = f.assign(score=(
        -f.vs_50d.abs() * 3
        - (f.rsi - 50).abs() / 100
        + (f.macdh > 0).astype(int) * 0.15
        + (f.macdh > f.macdh_prev3).astype(int) * 0.1
        + f.sma50_rising.astype(int) * 0.1
        - f.peg / 20
    )).sort_values("score", ascending=False)

    f.to_csv(OUT_CSV, index=False)
    print(f"\n通过入场区间筛选: {len(f)} / {len(df)}\n")
    print(f"{'Tkr':<6}{'Name':<24}{'Sector':<13}{'Px':>8}{'v50d%':>7}{'v200d%':>8}"
          f"{'RSI':>6}{'MACDh':>7}{'off52wH':>8}{'PEG':>6}{'PEf':>6}{'Gr%':>6}{'量能':>7}")
    for _, r in f.head(15).iterrows():
        print(f"{r.ticker:<6}{str(r['name'])[:23]:<24}{str(r.sector)[:12]:<13}"
              f"{r.price:>8.1f}{r.vs_50d*100:>7.1f}{r.vs_200d*100:>8.1f}"
              f"{r.rsi:>6.0f}{r.macdh:>7.2f}{r.off_52w_high*100:>8.1f}"
              f"{r.peg:>6.2f}{r.pe_fwd:>6.1f}{r.revenue_growth*100:>6.1f}"
              f"{r.vol_dryup*100:>6.0f}%")
    print(f"\n完整结果: {OUT_CSV}")


if __name__ == "__main__":
    main()
