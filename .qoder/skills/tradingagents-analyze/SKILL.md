---
name: tradingagents-analyze
description: 带数据源健康门禁的 TradingAgents 深度分析。执行任何 TradingAgents 分析（单标的或批量）前必须先检查所有数据源（StockTwits/Reddit/Polymarket/FRED/Yahoo/LLM 端点）可用，FAIL 则修复；分析过程/结束后扫描日志确认无数据源告警，否则修复重跑。只有数据源全程正常跑出的结果才允许交付。触发词：深度分析、跑 TradingAgents、分析 CVS/SPY/黄金、数据源检查、数据源修复后重跑。
---

# TradingAgents 数据源健康门禁深度分析

## 概述

保证 TradingAgents 管线（4 分析师 + 多空辩论 + 交易员 + 风控 + PM）只在
**所有数据源健康**的前提下运行，并在交付前验证运行日志无数据源告警。
数据源不干净时**不提供最终分析结果**。

## 门禁流程（必须按序执行）

### 1. Pre-flight：数据源健康检查

运行：

```bash
cd <仓库根>
.venv/bin/python .qoder/skills/tradingagents-analyze/scripts/check_data_sources.py [TICKER]
```

- 全部 `[OK]` + `GATE: all data sources healthy` → 进入第 3 步。
- 任一 `[FAIL]` → **禁止分析**，进入第 2 步。

### 2. 修复数据源

按 `references/data-sources.md` 的故障模式排查（常见：403 = Worker 被剥
X-Proxy-Key / DIRECT_HOSTS 缺失；`_worker_alive` False = .env 未加载导致 key 为空；
worker.dev 失效 = 重新 wrangler deploy；LLM 端点失败 = 检查 DASHSCOPE_CN_API_KEY）。

修改代码后**重新运行第 1 步**，直到全绿。

### 3. 深度分析

用仓库内的分析入口运行（均自带 `net_bootstrap` + `data_source_guards`）：

- 单标的：`.venv/bin/python scripts/run_cvs_analysis.py`
- 批量：`.venv/bin/python scripts/batch_analyze.py [YYYY-MM-DD] [--user "备注"] TICKER...`

必须注入用户持仓上下文（股数/成本/减仓线/清仓线/目标价），让 PM 结合真实仓位裁决。

### 4. Post-flight：验证运行日志

分析完成后，把完整 stdout（或报告的 message_tool.log）扫描一遍，确认**没有**出现
`references/data-sources.md` 里的失败信号（如 `StockTwits fetch failed`、`HTTP Error 429`、
`Polymarket search failed`、`unreachable from this network`、`rate-limited` 等）。

- 无失败信号 → 结果干净，进入第 5 步交付。
- 有失败信号 → **该轮结果作废**，修复数据源（第 2 步）后从第 3 步重跑，再次 Post-flight。

### 5. 交付

只交付通过了第 1 步（前置检查）+ 第 4 步（运行验证）的分析结果。
汇报时说明：数据源后端、检查结论、运行中无告警。任何一步未过，回复：

> 数据源未完全健康（<列出 FAIL>），为保证分析质量已暂停交付；正在修复并重跑。

## 数据源失败信号速查（判断"合法空结果" vs "故障"）

- 合法空结果（不是故障）：`no Reddit posts mentioning`、`no posts found mentioning`、
  Manifold 替代的标注块、`0 posts`。
- 故障信号：`fetch failed`、`HTTP Error 4\d\d`、`429`、`timed out`、
  `Max retries exceeded`、`unreachable from this network`、`rate-limited`、
  `<stocktwits unavailable`、`Polymarket search failed`。

## 资源

- `scripts/check_data_sources.py` — 数据源健康检查（含 LLM 端点探测）
- `references/data-sources.md` — 各数据源故障模式与修复手册
