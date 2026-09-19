# 快速开始

本指南覆盖两条路径：

1. **一分钟试玩**（推荐新手）：`cne init --profile demo`，小宇宙、独立目录，几分钟出真数  
2. **全量数据湖**：`cne config create` → `cne init` → `cne run daily --all-groups`（耗时长、占磁盘）

详细选项见 [CLI 参考](../reference/cli.md)。安装见 [installation](installation.md)。

## 0. 一分钟试玩（可选）

不必 clone 仓库：

```bash
pip install cnequity
cne init --profile demo
# 可选：再看一根完整 1m 会话
# cne init --profile demo --intraday
```

如果当前网络无法连接 TDX，可先用确定性的离线样例验证安装、Parquet 落盘和查询链路：

```bash
cne init --profile sample
```

该模式不访问网络，生成的合成行全部标记为 `source=mock`，只能用于上手验证，不能用于研究。日期不参与新鲜度门禁；`audit` 仍展示 mock 证据，但当作该 profile 的预期 info，而不是安装失败。

会写入独立的 `data/cnequity-demo/` 与 `configs/cnequity.demo.toml`。  
**不要**把 demo 的 `data_root` 拿去跑全量 `cne init`。
如果 `configs/cnequity.demo.toml` 已存在且指向别的湖，命令会保留原文件并要求改用
`--config-out`；只有显式 `--force` 才会覆盖。内容完全相同时允许直接重跑。

接着可查：

```bash
cne query --config configs/cnequity.demo.toml --sql "
  SELECT symbol, trade_date, close, volume, source
  FROM daily_bars
  ORDER BY trade_date DESC
  LIMIT 10
"
```

只想验证复权研究口径，不必初始化全市场：

```bash
cne init --profile demo --research --symbols 600519.SH
```

research demo 会把窗口扩展到约三年，读取 Sina 的 hfq 因子，并打印 raw return 与 hfq return 的对照。
它需要额外访问 Sina；网络受限时，先使用不带 `--research` 的基础 demo。

下面从第 1 步起是全量湖路径。

## 1. 准备全量配置

```bash
pip install cnequity # 若尚未安装
cne config create    # → configs/cnequity.toml；macOS / Windows 自动 workers=1
# 可选：cne config create --data-root /abs/path/to/lake
cne config validate
```

按需编辑 `configs/cnequity.toml` 里的 `data.root`（生产建议绝对路径）。

> 源码开发：也可 `cp configs/cnequity.example.toml configs/cnequity.toml`，与 `cne config create` 等价。

## 2. 初始化数据湖

```bash
cne init --config configs/cnequity.toml
```

`init` 会：

1. 创建 `{data.root}` 下 staging / curated / derived / meta / duckdb 目录  
2. 初始化 `meta/manifest.db`（SQLite WAL）与 DuckDB 视图  
3. 按 `[job.init.phases]` 执行分阶段全量回填（默认最近 3 年、全市场标的）

需要从 2016 年起的完整初始化时，使用 `cne init --profile full`；也可以先用默认窗口建湖，再按需回填。

**仅建目录、不跑回填：**

```bash
cne init --layout-only --config configs/cnequity.toml
```

init 耗时较长（全市场日线分页回填），建议在稳定网络下运行。阶段定义见 [数据流 — Init](../architecture/data-flow.md#init全量回填)。

<a id="init-scope"></a>

### Init：范围、磁盘与续跑

默认 `cne init`（`--profile quick`）是沪深京全市场、近 3 年的主干，不是只拉 400 只。`--profile full` 仍是全市场，只是把主干加深到各数据集自己的历史起点，其中日线从 2016-01-01 起。

| 命令 | 实际范围 | 参考耗时 | 磁盘量级 |
|---|---|---:|---|
| `cne init --profile demo` | 5 只 × 最近约 30 个交易日，独立 demo 湖 | 实测约 25 秒 | 数 MB |
| `cne init`（`--profile quick`） | 沪深京全市场（5,000+ 只）× 最近 3 年；证券、日历、日线、交易状态等主干 | 通常约 1 小时 | 日频主干通常几百 MB |
| `cne init --profile full` | 还是全市场；主干按各数据集默认起点拉取，日线从 2016-01-01 起 | 通常约 3 小时，约为 quick 的 3 倍 | 日频更长，仍是几百 MB 到约 1 GB |
| `cne backfill trading_status` | 补齐全市场历史 ST 证据；约 5,500 只 | 整轮约 10–11 小时；同范围 init 已扫 400 只后通常还需约 9–10 小时 | 增量很小 |

这些是实测量级，不是时限承诺。TDX / Baostock 连不连得上、出口位置、上游限流、重试次数和机器配置都会改变耗时；以命令打印的批次进度和 ETA 为准。

磁盘可以这样对照：全市场日频 2001–2026 合计约 **468 MB**。运行中的 staging 和 revision 会再占一份；日积月累、又开了分钟线的生产湖可以到十几 GB。分钟线、5 分钟线和分笔默认关闭：全市场 1 分钟线约 **8.4 GB/年**，比整座日频湖大约一个数量级。详见[运行手册 · 日内数据](../operations/runbook.md#日内数据minute_bars--minute_bars_5m)。

> **进度里出现 400 只，并不表示 init 只拉了 400 只。** 日线、证券列表等主干仍然扫描全市场。`400` 只限制最慢的 **Baostock 历史 ST 状态**：免费接口限速很明显，所以首次 `init` 每轮先扫 400 **只证券**（不是 400 条数据）就暂停，避免新用户多等十小时。进度写入 checkpoint；显式运行 `cne backfill trading_status` 会自动取消这 400 只上限。同一历史起止日和 universe 会从 checkpoint 继续；改变范围会按新范围重新开一轮。

新湖如果希望主干数据和历史 ST 都达到项目约定的完整范围，按顺序跑：

```bash
cne init --profile full --config configs/cnequity.toml
cne backfill trading_status --config configs/cnequity.toml

# 再抓其余日更组，并从此每个交易日执行
cne run daily --all-groups --config configs/cnequity.toml
```

前两条最好同一天接着跑。如果要跨天续跑，给 `init --trade-date` 和 `backfill --end` 传同一个截止日，并保持 2016-01-01 起点不变，才能接着同一份 checkpoint。

这里说的「完整」不是「42 个数据集都有无限历史」。项目没有一条命令能把所有数据集的全部历史一次拉完。`init` 负责证券、日历、公司行为、个股/指数日线、交易状态和派生因子这些主干；分钟线、5 分钟线和分笔默认关闭，快照型数据也无法回补源端没有提供的历史。某个可回补的数据集需要更早的历史时，使用 `cne backfill <dataset> --start ... --end ...`；各数据集能拉到哪一年，见[数据集目录](../datasets/catalog.md)。

如果中途按了 Ctrl-C，不要删除 `data/`，也不需要从头开始。原命令再跑一次会自动找到未完成的 run，并从失败批次继续；也可以显式指定：

```bash
cne init --profile full --config configs/cnequity.toml
# 或：
cne init --resume --config configs/cnequity.toml
cne run retry --run-id RUN_ID --config configs/cnequity.toml
cne status --datasets --config configs/cnequity.toml
```

Ctrl-C 之后，命令会先停住正在跑的 worker，再把没跑完的批次记成可以马上重试的 `failed`；已经成功的批次不会重拉。进程被直接杀掉也没关系：下一次命令会根据运行锁认出这个孤儿 run。某个阶段没跑完，后面的阶段和 compact 都不会开始，所以一次小范围的成功回填，不会把只覆盖部分证券的数据说成「初始化完整」。

`status --datasets` 里的 `fresh` 只表示**已经落盘的那些日期**够新。正式数据湖还会用最新日线截面，对照配置的 `[universe].ingest`、当天 active 证券，以及明确的停牌证据，做一次轻量核对：范围内每只 active 证券都得有日线，或者有明确停牌证据。如果缺了整个配置市场（例如 `all_a` 没有 BJ），初始化会在 instruments 阶段提前失败。init 没跑完、任何一只证券缺证据、或者截面核对不上，都会另外报警并返回非零。

「全市场」含北交所：上市状态、停复牌与 ST 取自北交所自己的板块页，日线历史走 TDX，成交额由 TDX 补齐（Sina 从未发布过这一列）。默认 universe `all_a` 覆盖沪、深、京三市的 A 股。每个数据集真正拉到哪一天，会记在 `coverage_start`。

需要更长历史时，可以一次拉满，也可以以后再补深：

```bash
cne init --profile full

# 或对单个数据集补历史
cne backfill daily_bars --start 2016-01-01 --end COVERAGE_START
```

**它跑到哪了？** 运行中会打这几类行，正常情况下不会连续静默超过 60 秒：

| 行 | 含义 |
|------|------|
| `Step <名字> starting` / `Step <名字> success in Ns` | 步骤进出 |
| `daily_bars: 5,283 symbol(s) over … → 53 batch(es) … on 4 lane(s)` | 这一趟扫描的规模，开跑前就打出来 |
| `daily_bars 8/53 batches · 12,400 rows · 1m24s elapsed · ~7m55s left` | 滚动进度；凑满一轮 lane 后才给剩余时间 |
| `still working: daily_bars 4m12s (no output for 1m02s)` | 心跳，静默满 60 秒时点名当前步骤 |

启动时打印的 `Logging to …/logs/cne-init-<时间戳>.log` 是本次运行的日志文件（目录可用 `CNE_LOG_DIR` 覆盖）。另开一个终端也可以查：

```bash
cne status --run latest --config configs/cnequity.toml
```

**成本按标的数算，不按天数算。** `daily_bars` 是逐标的抓取，所以 `--start D --end D` 只拉一天，付出的仍是全市场（约 5,300 个标的、50+ 个批次）的一整趟扫描，实测十分钟级别，和多年窗口的差别只在每个标的返回多少根 K 线。想快速验证请用 `--symbols` 缩小范围：

```bash
cne backfill daily_bars --symbols 600519.SH,000001.SZ --start 2026-09-15 --end 2026-09-15
```

## 3. 回填验收（推荐，需仓库脚本）

验收脚本在 GitHub 仓库的 `scripts/`，**不随 PyPI 包安装**。有 checkout 时：

```bash
git clone https://github.com/rootSunc/CNEquity.git
cd CNEquity
python scripts/accept_backfill.py snapshot --out /tmp/curated-counts.json
# 同窗口重跑 daily 后对比
python scripts/accept_backfill.py check --compare /tmp/curated-counts.json
```

验收项：幂等性、覆盖起点、消费层可读。详见 [回填完成验收](../operations/runbook.md#回填完成验收)。

纯 PyPI 用户可先用 `cne status --datasets` / `cne stats show` 做粗检。

## 4. 每日增量

日更按**调度组**执行，一天 6 个组。生产就是这么跑的：

```bash
cne run daily --all-groups --config configs/cnequity.toml
```

`--all-groups` 按配置顺序串行跑完全部组：某个组失败不会中断后面的组，退出码取最差的一个，
数据集全部关闭的组（默认的 `intraday` / `ticks`）自动跳过。也可以一个一个跑：

```bash
cne run daily --group core --config configs/cnequity.toml
cne run daily --group capital --config configs/cnequity.toml
cne run daily --group signals --config configs/cnequity.toml
cne run daily --group fundamentals --config configs/cnequity.toml
cne run daily --group macro_risk --config configs/cnequity.toml
cne run daily --group research --config configs/cnequity.toml
```

每组末尾含 `compact`，数据会写入 curated。组定义见 [配置 — 调度组](configuration.md#调度组)。
按组挂 cron 时请**错开**（见 [运行手册](../operations/runbook.md)），不要让六个组同时打同一批上游。

> **不带 `--group` / `--all-groups` 的 `cne run daily` 不等于「全部组」。**
> 它只跑 `[[job.daily.waves]]` 里的核心骨架 —— 行情、日历、交易状态、公司行为、复权 ——
> 不包含估值、财报、融资融券、龙虎榜、北向、指数成分等其余数据集。只跑这一条，
> 湖会安静地停在 15/42 新鲜，且不会报错。跑完会打印一行提示，列出本次没有覆盖的数据集。

非交易日自动跳过（`skipped_non_trading_day`，退出码 0）。

有仓库 checkout 时，`scripts/daily_pipeline.sh` 会按依赖顺序跑完全部分组并做收尾
（健康检查、源探测、元数据备份），一条 cron 即可；该脚本不随 PyPI 包安装。

## 5. 查看状态

```bash
cne status --config configs/cnequity.toml            # 最近一次 run 摘要
cne status --datasets --config configs/cnequity.toml # 各数据集新鲜度
cne stats show --config configs/cnequity.toml        # 行数统计
```

## 6. 读取数据

### Python API（推荐）

```python
from cnequity.query import load

bars = load(
    "daily_bars",
    start="2024-01-01",
    end="2024-12-31",
    adjust="hfq",
    universe="all_a",
)

roe = load(
    "financial_statement_items",
    items=["roe"],
    as_of="2024-04-30",
)
```

见 [查询指南](../datasets/query-guide.md) 与 [Python API](../reference/python-api.md)。

### DuckDB SQL

```bash
cne query --sql "
  SELECT symbol, trade_date, adj_close
  FROM daily_bars_adj
  WHERE trade_date >= '2025-01-01'
" --config configs/cnequity.toml
```

数据库文件：`{data.root}/duckdb/cnequity.duckdb`。

### 直读 Parquet

```python
import polars as pl
df = pl.scan_parquet("data/cnequity/curated/daily_bars/**/*.parquet")
df.filter(pl.col("symbol") == "600519.SH").collect()
```

## 7. 失败重试

```bash
cne status --config configs/cnequity.toml    # 找到 failed run_id
cne run retry --run-id RUN_ID --config configs/cnequity.toml
```

retry 只重跑失败 batch；全部成功后自动 compact → derive_adj_factors → audit。

## 8. 生产调度（可选，需仓库脚本）

```bash
# 需 clone 仓库后：
scripts/install_scheduler.sh   # macOS launchd，每天 11:15 本机时间
```

见 [运维 Runbook](../operations/runbook.md)。

## 常见陷阱

| 问题 | 说明 |
|------|------|
| `load()` 读不到新数据 | 确认 run 已 compact；分组 run 必须含 `compact` step |
| `universe="all_a"` 未剔历史 ST | `trading_status` 仅覆盖日更起点之后；2016→上线日回测需注意 |
| init 中途失败 | 不要删 `data/`；再跑同一条 `cne init` 会自动续跑，也可以显式 `--resume` / `retry` |
| TDX 连接失败 | `cne sources probe --only tdx_protocol`；检查 `[tdx_protocol.hosts]` 与网络 |
| 缺配置报错 | 先跑 `cne config create` |
| demo 与全量混用 | demo 用独立 `data/cnequity-demo/`，全量另配 `data.root` |

更多排障：[troubleshooting](../operations/troubleshooting.md) · [runbook](../operations/runbook.md)。
