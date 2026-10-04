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
| 建立可日更的数据湖 | [快速开始](getting-started/quickstart.md) → [初始化与续跑](getting-started/initialization.md) → [运行手册](operations/runbook.md) |
| 在浏览器里查看、日更或备份 | [快速开始 · 浏览器](getting-started/quickstart.md#browser) → [定时任务与备份](operations/runbook.md#web-schedule) |
| 查有哪些数据、能补多远 | [数据集目录](datasets/catalog.md) → [数据源限制](datasets/sources.md) → [字段与单位](datasets/schema.md) |
| 研究复权、历史股票池或财报 | [查询指南](datasets/query-guide.md) → [研究示例](recipes/README.md) → [Python API](reference/python-api.md) |
| 研究商品期货与期权 | [衍生品指南](recipes/derivatives.md) |
| 让 AI agent 读取本地数据 | [MCP 接入与工具边界](reference/mcp.md) |
| 查某条命令怎么用 | [CLI 按任务索引](reference/cli.md) → [参数与默认值](reference/cli-options.md) → [联网与写入清单](reference/cli-surface.md) |
| 处理失败、限流或数据缺口 | [排障](operations/troubleshooting.md) → [源保护](operations/fetch-policy.md) → [源健康](operations/source-health.md) |
| 升级或反馈问题 | [更新日志](changelog.md) → [升级与兼容性](getting-started/installation.md#升级与兼容性) |

## 一条命令初始化

```bash
pip install cnequity
cne init
```

第一次运行自动生成 `configs/cnequity.toml`，然后建立沪深京全市场最近 3 年的主干（含窗口内已知退市股票的日线），审计后发布到当前目录的 `data/cnequity/`。全市场初始化可能需要数小时；中断或结果有 `warning` 时，重跑同一条 `cne init` 即续跑。详见[快速开始](getting-started/quickstart.md)。

## 先分清四件事

| 概念 | 含义 |
|---|---|
| 注册 ≠ 已采集 | 当前开发树有 52 个数据集（47 curated + 5 derived），包括可选、兼容与占位入口；安装不会附带数据 |
| 新鲜 ≠ 完整 | `fresh` 反映更新日期；历史缺口、ST 与退市证据、PIT 质量需要分别核验 |
| 交易日 ≠ 自然日 | `run daily` 交易日跑日更组，每天（含周末）跑公告和新闻事件流；每天调度一次即可 |
| 回填 ≠ 严格 PIT | 今日回填的财报不能证明过去已观察到该版本；研究显式使用 `pit_mode="strict"` |

## 产品方向

[方向与边界](architecture/overview.md)说明项目目标和能力限制；[设计原则](architecture/design-principles.md)解释数据质量、版本与可恢复性的承诺。

## 版本、来源与许可

文档随当前仓库实现更新，PyPI 稳定版可能落后。先运行 `cne --version`，再核对[更新日志](changelog.md)；命令参数可用本机 `cne ... --help` 确认。

代码 Apache-2.0，数据受各上游条款约束，详见[数据许可](legal-and-data-sources.md)与[来源矩阵](legal-and-data-sources.md#来源合规矩阵)。论文或报告使用时，请记录软件、数据版本与查询口径，见[引用项目](legal-and-data-sources.md#引用项目)。

欢迎[报告问题](https://github.com/rootSunc/CNEquity/issues)、修正文档，或在 [GitHub 点一个 Star](https://github.com/rootSunc/CNEquity)支持项目。
