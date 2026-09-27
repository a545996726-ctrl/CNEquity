---
title: CNEquity 文档
description: 从首个查询到持续采集、历史研究与数据质量验收，按任务查找 CNEquity 使用指南。
---

# CNEquity 文档

把多源中国市场数据持续写入自己的 Parquet 湖，通过 Python、DuckDB、Polars 与 AI agent 使用同一份数据。

**第一次来？** 从[快速开始](getting-started/quickstart.md)拿到第一条查询结果；了解项目价值与适用场景，见[项目 README](https://github.com/rootSunc/CNEquity#readme)。

## 按你要做的事找文档

| 我想…… | 阅读路径 |
|---|---|
| 先体验，不建全市场湖 | [安装](getting-started/installation.md) → [Demo / 离线样例](getting-started/quickstart.md) |
| 建立可日更的正式湖 | [初始化与续跑](getting-started/initialization.md) → [配置](getting-started/configuration.md) → [运行手册](operations/runbook.md) |
| 查有哪些数据、能补多远 | [数据集目录](datasets/catalog.md) → [数据源限制](datasets/sources.md) → [字段与单位](datasets/schema.md) |
| 研究复权、历史股票池或财报 | [查询指南](datasets/query-guide.md) → [研究示例](recipes/README.md) → [Python API](reference/python-api.md) |
| 研究商品期货与期权 | [衍生品指南](recipes/derivatives.md) |
| 让 AI agent 读取本地数据 | [MCP 接入与工具边界](reference/mcp.md) |
| 查某条命令怎么用 | [CLI 按任务索引](reference/cli.md) → [参数与默认值](reference/cli-options.md) → [联网与写入清单](reference/cli-surface.md) |
| 处理失败、限流或数据缺口 | [排障](operations/troubleshooting.md) → [源保护](operations/fetch-policy.md) → [源健康](operations/source-health.md) |
| 升级或反馈问题 | [更新日志](changelog.md) → [升级与兼容性](getting-started/upgrading.md) |

## 最短体验路径

```bash
pip install cnequity
cne init --profile demo
cne query --config configs/cnequity.demo.toml --sql "SELECT symbol, trade_date, close, source FROM daily_bars LIMIT 5"
```

默认 5 只股票、最近约 30 个交易日，独立目录；需要 TDX 连通性。网络受限时使用[离线 sample](getting-started/quickstart.md)，合成数据只用于验证链路。

## 先分清四件事

| 概念 | 含义 |
|---|---|
| 注册 ≠ 已采集 | 当前开发树有 52 个数据集（47 curated + 5 derived），包括可选、兼容与占位入口；安装不会附带数据 |
| 新鲜 ≠ 完整 | `fresh` 反映更新日期；历史缺口、ST 与退市证据、PIT 质量需要分别核验 |
| 日更 ≠ 事件流 | `run daily --all-groups` 遍历日更组；公告和新闻另用 `run events`，周末也运行 |
| 回填 ≠ 严格 PIT | 今日回填的财报不能证明过去已观察到该版本；研究显式使用 `pit_mode="strict"` |

## 产品方向

[方向与边界](architecture/overview.md)说明项目目标和能力限制；[设计原则](architecture/design-principles.md)解释数据质量、版本与可恢复性的承诺。

## 版本、来源与许可

文档随当前仓库实现更新，PyPI 稳定版可能落后。先运行 `cne --version`，再核对[更新日志](changelog.md)；命令参数可用本机 `cne ... --help` 确认。

代码 Apache-2.0，数据受各上游条款约束，详见[数据许可](legal-and-data-sources.md)与[来源矩阵](legal/source-matrix.md)。论文或报告使用时，请记录软件、数据版本与查询口径，见[引用项目](citation.md)。

欢迎[报告问题](https://github.com/rootSunc/CNEquity/issues)、修正文档，或在 [GitHub 点一个 Star](https://github.com/rootSunc/CNEquity)支持项目。
