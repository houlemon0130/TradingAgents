"""Sector / theme momentum screen (layer 0 of the funnel).

Ranks the 11 SPDR sectors plus a set of thematic ETFs by absolute and
SPY-relative momentum, then cross-references the S&P 500 valuation screen
(reports/sp500_screen.csv) to show which sectors are cheap *and* trending.

Usage: .venv/bin/python scripts/screen_sectors.py
"""
import pandas as pd
import yfinance as yf

SECTORS = {
    "XLK": "Technology", "XLV": "Health Care", "XLF": "Financials",
    "XLY": "Cons Discretionary", "XLP": "Cons Staples", "XLE": "Energy",
    "XLI": "Industrials", "XLB": "Materials", "XLU": "Utilities",
    "XLRE": "Real Estate", "XLC": "Comm Services",
}
THEMES = {
    "SMH": "Semiconductors", "IGV": "Software", "XBI": "Biotech",
    "IHI": "Medical Devices", "KRE": "Regional Banks", "ITA": "Aerospace/Defense",
    "GDX": "Gold Miners", "XME": "Metals & Mining", "URA": "Uranium",
    "ICLN": "Clean Energy", "IYT": "Transport", "XHB": "Homebuilders",
    "PAVE": "US Infrastructure", "ROBO": "Robotics/Automation", "FDN": "Internet",
    "XOP": "Oil&Gas E&P", "CIBR": "Cybersecurity", "SKYY": "Cloud",
}
BENCH = "SPY"
WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


def main():
    tickers = list(SECTORS) + list(THEMES) + [BENCH]
    px = yf.download(tickers, period="2y", interval="1d",
                     auto_adjust=True, progress=False)["Close"]
    px = px.dropna(how="all")
    print(f"history rows: {len(px)}  last close: {px.index[-1].date()}\n")

    rows = []
    for sym in tickers:
        if sym not in px or px[sym].dropna().empty:
            continue
        s = px[sym].dropna()
        b = px[BENCH].dropna()
        row = {"ticker": sym,
               "name": SECTORS.get(sym) or THEMES.get(sym) or "S&P 500",
               "kind": "sector" if sym in SECTORS else ("bench" if sym == BENCH else "theme"),
               "price": s.iloc[-1]}
        for label, n in WINDOWS.items():
            if len(s) > n:
                row[f"r_{label}"] = s.iloc[-1] / s.iloc[-1 - n] - 1
                row[f"rel_{label}"] = row[f"r_{label}"] - (b.iloc[-1] / b.iloc[-1 - n] - 1)
        ma200 = s.rolling(200).mean().iloc[-1]
        ma50 = s.rolling(50).mean().iloc[-1]
        row["vs_200d"] = s.iloc[-1] / ma200 - 1
        row["vs_50d"] = s.iloc[-1] / ma50 - 1
        hi52 = s.iloc[-252:].max() if len(s) >= 252 else s.max()
        row["off_52w_high"] = s.iloc[-1] / hi52 - 1
        # momentum score: blend of SPY-relative 3m/6m/12m
        row["score"] = (0.5 * row.get("rel_3m", 0) + 0.3 * row.get("rel_6m", 0)
                        + 0.2 * row.get("rel_12m", 0))
        rows.append(row)

    df = pd.DataFrame(rows).sort_values("score", ascending=False)
    df.to_csv("reports/sector_screen.csv", index=False)

    def show(sub, title):
        print(f"== {title} ==")
        print(f"{'Tkr':<6}{'Name':<22}{'1m%':>7}{'3m%':>7}{'6m%':>7}{'12m%':>8}"
              f"{'rel3m':>7}{'rel12m':>8}{'v200d':>7}{'off52wH':>8}")
        for _, r in sub.iterrows():
            print(f"{r['ticker']:<6}{r['name'][:21]:<22}"
                  f"{r.get('r_1m', float('nan'))*100:>7.1f}{r.get('r_3m', float('nan'))*100:>7.1f}"
                  f"{r.get('r_6m', float('nan'))*100:>7.1f}{r.get('r_12m', float('nan'))*100:>8.1f}"
                  f"{r.get('rel_3m', float('nan'))*100:>7.1f}{r.get('rel_12m', float('nan'))*100:>8.1f}"
                  f"{r['vs_200d']*100:>7.1f}{r['off_52w_high']*100:>8.1f}")
        print()

    show(df[df.kind == "bench"], "benchmark")
    show(df[df.kind == "sector"], "sectors (by SPY-relative momentum)")
    show(df[df.kind == "theme"], "themes (by SPY-relative momentum)")

    # Cross-reference valuation screen
    try:
        v = pd.read_csv("reports/sp500_screen.csv")
    except FileNotFoundError:
        print("no reports/sp500_screen.csv — skip valuation cross-ref")
        return
    v = v[(v.pe_ttm > 0) & (v.pe_ttm < 60) & (v.peg > 0) & (v.peg < 3)
          & (v.revenue_growth > 0) & (v.fcf_b > 0) & (v.above_200dma)]
    agg = v.groupby("sector").agg(
        n=("ticker", "count"), peg_med=("peg", "median"),
        pe_fwd_med=("pe_fwd", "median"), growth_med=("revenue_growth", "median"),
    ).sort_values("peg_med")
    print("== S&P500 screen passers grouped by sector (valuation side) ==")
    print(f"{'Sector':<26}{'n':>4}{'PEG_med':>9}{'PEfwd':>8}{'Gr%':>7}")
    for sec, r in agg.iterrows():
        print(f"{sec[:25]:<26}{int(r['n']):>4}{r['peg_med']:>9.2f}"
              f"{r['pe_fwd_med']:>8.1f}{r['growth_med']*100:>7.1f}")
    print("\nfull results: reports/sector_screen.csv")


if __name__ == "__main__":
    main()
