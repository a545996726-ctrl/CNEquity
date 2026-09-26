# 快速开始：从安装到第一条查询

先用独立的小湖验证采集和读取，再决定是否建立全市场湖。下列命令无需克隆仓库；Python 和系统要求见[安装](installation.md)。

## 1. 选择体验方式

安装：

```bash
pip install cnequity
cne doctor
```

**真实数据（推荐）**：需要连接 TDX 行情主机。

```bash
cne init --profile demo
```

默认范围是 5 只股票、最近约 30 个交易日。成功后得到 `data/cnequity-demo/` 和 `configs/cnequity.demo.toml`，终端会打印样例。不要用这个五股湖继续跑全市场初始化。

**网络受限时的离线样例**：

```bash
cne init --profile sample --data-root data/cnequity-sample --config-out configs/cnequity.sample.toml
```

该模式不请求上游，生成的行全部标记为 `source=mock`，只用于验证安装、Parquet 和查询链路。使用上面的独立路径，可与真实 demo 并存；若不指定路径，sample 与 demo 的默认输出路径相同。已有不同内容的配置会被保留，命令要求换 `--config-out`，不会静默覆盖。

## 2. 读到第一条结果

下面读取真实 demo；离线样例请把路径换为 `data/cnequity-sample`，CLI 配置换为 `configs/cnequity.sample.toml`。

```python
from cnequity.query import load

bars = load("daily_bars", data_root="data/cnequity-demo")
print(bars.select("symbol", "trade_date", "close", "volume", "source").tail(10))
```

```bash
cne query --config configs/cnequity.demo.toml --sql "SELECT symbol, trade_date, close, source FROM daily_bars ORDER BY trade_date DESC LIMIT 10"
```

能看到日期、价格和来源，就已跑通“采集 → 落盘 → 查询”。基础 demo 不含完整财报、历史股票池或复权因子，不能据此判断正式湖的覆盖。

## 3. 在浏览器检查

```bash
cne serve --config configs/cnequity.demo.toml
```

打开 <http://127.0.0.1:8787>，查看数据集、水位和样例行；按 `Ctrl-C` 停止服务。控制台不执行采集或修复。

想验证复权，可另外运行：

```bash
cne init --profile demo --research --symbols 600519.SH
```

它把窗口扩展至约三年，并额外从 Sina 读取 hfq 因子。详细的 Python 复核见[复权研究基线](../recipes/research-baseline.md)。

## 4. 建立正式数据湖

在长期使用的工作目录执行，保持正式湖与 demo 分开：

```bash
cne config create
cne config validate
cne init
cne run daily --all-groups
cne run events
cne status --datasets
```

已有正式配置时跳过 `config create`。默认 `init` 是沪深京全市场近三年的初始化主干，可能需要较长时间；`--profile full` 加深历史，其中日线从 2016-01-01 起。它不会自动填满所有注册数据集。

| 后续操作 | 作用 |
|---|---|
| `cne run daily --all-groups` | 交易日按配置顺序运行日更组；全关的可选组跳过 |
| `cne run events` | 单独更新公告、监管事件、新闻；非交易日也运行 |
| `cne status --datasets` | 查新鲜度与缺口提示 |
| `cne run retry --run-id RUN_ID` | 重试指定 run 的失败批次 |

裸 `cne run daily` 只跑核心 waves。分钟线、分笔与逐合约衍生品默认关闭；`fresh` 也不等于历史完整。全量审计、历史 ST 和退市覆盖见[初始化指南](initialization.md)。

<a id="init-scope"></a>

## Init：范围、磁盘与续跑

这部分已整理到[初始化、范围与续跑](initialization.md#init-scope)：包含 quick/full 的区别、400 只历史 ST 扫描上限、成本、Ctrl-C 后恢复、日志和验收。旧链接保留在这里，便于从已有资料跳转。

## 遇到问题

| 现象 | 下一步 |
|---|---|
| TDX 无法连接 | `cne doctor` 离线体检；再对 demo 配置执行一次 `cne sources probe --only tdx_protocol` |
| `no parquet data` | 检查使用的配置、绝对 `data.root` 与该数据集是否已 compact |
| 已有配置不允许覆盖 | 换 `--config-out`；已有正式配置用 `cne config diff` 审阅，不覆盖重建 |
| 初始化中断 | 保留数据，重跑同一条 `cne init`；详见[续跑](initialization.md) |
| 历史 PIT 没有行 | 刚回填的数据未必是过去已观察到的证据，见[PIT 示例](../recipes/pit-rebalance.md) |

接下来：[研究示例](../recipes/README.md) · [配置](configuration.md) · [运行手册](../operations/runbook.md) · [排障](../operations/troubleshooting.md)。
