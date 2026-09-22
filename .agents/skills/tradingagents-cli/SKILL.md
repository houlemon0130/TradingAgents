---
name: tradingagents-cli
description: Run this repository's TradingAgents CLI for single-ticker research, portfolio-aware analysis, checkpoint recovery, or historical backtests. Use when the user asks to run their TradingAgents project or its CLI; do not use for ordinary market questions that do not request the project.
---

# TradingAgents CLI

Use the repository's installed CLI as the execution boundary. Do not replace it with a temporary agent, an ad-hoc Python pipeline, `webui_run.py`, or the older `.qoder` scripts.

## Locate and verify the project

Work from the repository whose `pyproject.toml` exposes:

```toml
[project.scripts]
tradingagents = "cli.main:app"
```

Prefer `.venv/bin/tradingagents`; fall back to `python -m cli.main` only when the project virtual environment is absent. Run `--help` before the first invocation if the checkout may have changed.

The CLI is a research system. It does not connect to a broker or place orders. Describe its result as analysis or a model decision, never as an executed trade.

## Choose the CLI mode

- For one ticker and one analysis date, run the bare interactive command.
- Add `--portfolio <json>` only when the user supplied or approved an existing portfolio file. Do not invent holdings, cost basis, cash, or thresholds.
- Use `backtest` only when the user asks to evaluate decisions over a historical ticker/date grid.

Use checkpointing for a normal single-ticker run because it is long and network/model failures can be recoverable:

```bash
.venv/bin/tradingagents --checkpoint
.venv/bin/tradingagents --checkpoint --portfolio /absolute/path/to/portfolio.json
```

The command is interactive, so run it in a PTY and respond to its prompts. Reuse remembered defaults when they match the user's request. Never pass `--clear-checkpoints` unless the user explicitly asks for a fresh run or stale checkpoint state is proven to be the cause of a failure; explain that clearing removes saved recovery state.

## Answer the interactive prompts

Map user intent directly to the prompts:

1. Ticker: use the canonical Yahoo-style symbol, including exchange suffixes (`0700.HK`, `600519.SS`) or crypto form (`BTC-USD`). Do not silently substitute a different instrument when identity is ambiguous.
2. Date: use `YYYY-MM-DD`, never a future date. For “today/current”, use the current local date and state it in the final answer.
3. Output language: use the user's language unless they requested another.
4. Analysts: select all available analysts for a full analysis unless the user asks for a narrower or cheaper run. Crypto runs omit fundamentals because the CLI filters it out.
5. Research depth: shallow for a quick/cheap check, medium as the default balance, deep only when requested or when the decision stakes justify the extra cost and latency.
6. Provider/models: reuse the project's remembered or environment-configured provider and models unless the user specifies alternatives. Never request API keys, tokens, or secret contents in chat. If credentials are missing, report the exact missing environment variable and let the user configure it locally.
7. Save report: accept the default save path unless the user supplied a destination. Do not overwrite an existing report.

Do not hide a long-running analysis merely because the terminal is quiet. Check process state and newly written run files before deciding it is stuck. Continue polling with bounded waits while progress is observable.

## Backtest

Use the CLI contract directly:

```bash
.venv/bin/tradingagents backtest NVDA,AAPL \
  --start 2026-06-01 --end 2026-08-01 --every 7
```

Optional flags are `--analysts`, `--asset-type`, `--portfolio`, and `--run-id`. Reuse `--run-id` to continue an interrupted sweep; do not start a duplicate sweep when the prior run is recoverable. A historical analysis date fixes price/indicator windows, but live news and social inputs can still change, so do not claim bit-for-bit reproducibility.

## Validate and report

After the CLI exits:

- Confirm exit status and capture the saved report or run directory under the configured `TRADINGAGENTS_RESULTS_DIR` (default `~/.tradingagents/logs`).
- Read the final decision and the evidence sections that support it. If Trader action and Portfolio Manager rating differ, report both with their labels rather than collapsing them into one signal.
- Call out unavailable or degraded sources such as StockTwits `403`, Reddit `429`, timeouts, or provider fallback. A failed source is missing evidence, not neutral sentiment.
- Separate verified run facts from your interpretation. Include ticker, analysis date, asset/session context, provider/model if visible, report path, and any checkpoint resume.
- For backtests, report cells run/skipped, failures/unsettled cells, log path, and the CLI's summary. Do not market the output as a guaranteed strategy return.

Lead with the decision in plain language, then give the main supporting and opposing evidence, important data gaps, and the result location.
