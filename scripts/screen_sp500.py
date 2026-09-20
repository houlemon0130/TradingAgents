"""S&P 500 quantitative long-term pre-screen.

Pulls valuation/growth/cashflow/trend data via yfinance (free), filters to
long-term-friendly names, ranks by PEG, writes CSV + prints Top 20.
Usage: .venv/bin/python scripts/screen_sp500.py
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import yfinance as yf

N_WORKERS = 10
OUT_CSV = "reports/sp500_screen.csv"


def fetch(sym, session):
    for attempt in range(3):
        try:
            info = yf.Ticker(sym, session=session).info
            if info.get("marketCap"):
                return sym, info
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return sym, None


def main():
    src = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
           "master/data/constituents.csv")
    comp = pd.read_csv(src)
    symbols = sorted(comp["Symbol"].unique().tolist())
    print(f"components: {len(symbols)}", flush=True)

    session = requests.Session()
    session.headers["User-Agent"] = "Mozilla/5.0"
    rows = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=N_WORKERS) as ex:
        futs = [ex.submit(fetch, s, session) for s in symbols]
        for done, fut in enumerate(as_completed(futs), start=1):
            sym, info = fut.result()
            if info:
                rows.append(_pick(sym, info))
            if done % 50 == 0:
                print(f"  {done}/{len(symbols)} ({time.time()-t0:.0f}s)", flush=True)

    df = pd.DataFrame([r for r in rows if r])
    if df.empty:
        print("no data fetched — network issue?")
        return
    df.to_csv(OUT_CSV, index=False)

    # Filter: long-term friendly
    f = df
    f = f[f["market_cap_b"] >= 5]
    f = f[(f["pe_ttm"] > 0) & (f["pe_ttm"] < 60)]
    f = f[(f["peg"] > 0) & (f["peg"] < 3)]
    f = f[f["revenue_growth"] > 0]
    f = f[f["fcf_b"] > 0]
    f = f[f["above_200dma"] == True]  # noqa: E712
    f = f.sort_values("peg")

    print(f"\nfetched {len(df)}, passed filter {len(f)}")
    print(f"\n{'Ticker':<7}{'Name':<28}{'Sector':<14}{'Price':>8}{'PEG':>6}"
          f"{'PE_t':>7}{'PE_f':>7}{'Gr%':>6}{'FCF$b':>7}{'vs200d%':>8}")
    for _, r in f.head(20).iterrows():
        print(f"{r['ticker']:<7}{r['name'][:27]:<28}{r['sector'][:13]:<14}"
              f"{r['price']:>8.1f}{r['peg']:>6.2f}{r['pe_ttm']:>7.1f}"
              f"{r['pe_fwd']:>7.1f}{r['revenue_growth']*100:>6.1f}"
              f"{r['fcf_b']:>7.1f}{r['vs_200d']*100:>7.1f}%")
    print(f"\nfull results: {OUT_CSV}")


def _pick(sym, info):
    def num(*keys):
        for k in keys:
            v = info.get(k)
            if v is not None and v == v:  # not NaN
                return float(v)
        return None

    price = num("currentPrice", "regularMarketPrice") or 0
    d200 = num("twoHundredDayAverage")
    fcf = num("freeCashflow")
    return {
        "ticker": sym,
        "name": info.get("longName") or info.get("shortName") or sym,
        "sector": info.get("sector") or "?",
        "price": price,
        "pe_ttm": num("trailingPE"),
        "pe_fwd": num("forwardPE"),
        "peg": num("pegRatio"),
        "revenue_growth": num("revenueGrowth"),
        "fcf_b": fcf / 1e9 if fcf else None,
        "market_cap_b": (num("marketCap") or 0) / 1e9,
        "above_200dma": bool(d200 and price > d200),
        "vs_200d": (price / d200 - 1) if d200 else None,
    }


if __name__ == "__main__":
    main()
