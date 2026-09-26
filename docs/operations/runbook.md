# 运维 Runbook

面向已建立正式湖的使用者：调度、健康门禁、备份恢复与回填验收。首次建湖见[初始化指南](../getting-started/initialization.md)。

| 任务 | 入口 |
|---|---|
| 仅使用 PyPI 包 | `cne run daily --all-groups` 与独立的 `cne run events`，由系统调度器分别调用 |
| 使用仓库运维脚本 | 下方安装调度；脚本额外处理门禁、通知和元数据备份 |
| 临时失败 | [排障](troubleshooting.md)，先确定失败 run / 批次，再按范围重试 |
| 扩大历史 | [初始化与续跑](../getting-started/initialization.md)，回填先审阅 `--plan` |


## 组件一览

| 能力 | 脚本 | 作用 |
|------|------|------|
| 调度 | `scripts/daily_pipeline.sh` | 按配置顺序串行跑已启用日更组 + 健康检查 + 备份 |
| 调度 | `scripts/install_scheduler.sh` | 安装 macOS launchd（每个交易日北京时间 `[job.daily] run_at` 之后跑一次，与本机时区无关） |
| 调度 | `scripts/uninstall_scheduler.sh` | 卸载 launchd |
| 告警 | `scripts/health_notify.sh` | 日常审计、每周全湖质量检查 + 分组 freshness + macOS 通知 |
| 备份 | `scripts/backup_meta.sh` | manifest + state + quality 的 tar 轮换 |

脚本使用仓库 `.venv/bin/cne`，路径相对仓库根目录自解析。

## 安装调度

```bash
cd /path/to/cnequity
scripts/install_scheduler.sh
```

安装器将主机选择与模板分开：新安装按配置选择已启用组；重装保留已安装 daily 的
`CNE_GROUPS`、`CNE_SOURCE_VANTAGE`、自定义配置和各任务的执行时间。
明确覆盖组别和出口时使用环境变量；海外主机首次安装例如：

```bash
CNE_GROUPS=core CNE_SOURCE_VANTAGE=overseas scripts/install_scheduler.sh
scripts/install_scheduler.sh --check
scripts/install_scheduler.sh --dry-run /tmp/cnequity-scheduler-review
scripts/install_scheduler.sh --daily-only                  # 只同步现有日任务，不新增晚间任务
scripts/install_scheduler.sh --stale-only                  # 只同步补抓任务
```

**运行时间按北京时间配置，与本机时区无关。** 日更和补抓都每小时唤醒一次（日更每小时第 7 分钟、
补抓第 37 分钟），由 `scripts/scheduler_gate.py` 判断这次是不是「正式那一次」：

```toml
[job.daily]
run_at = "17:30"   # 北京时间；每个交易日过了这个点跑一次（默认 17:30）
[job.stale]
run_at = "21:00"   # 北京时间；当天日更跑过之后，只重试失败的快照数据（默认 21:00）
```

- 每个交易日各跑一次：过了 `run_at` 就跑，直到下一个交易日 09:15 开盘前都算这一天的；
  过了开盘还没跑（比如电脑一直关着），这一天就不补了——历史类数据下次日更按日期补齐，快照类缺这一天。
- 本机是哪个时区、有没有夏令时都不用管，也不用重装；电脑在 `run_at` 时睡着，醒来后下一次唤醒就会跑。
- 手工执行 `scripts/daily_pipeline.sh` 不受这个判断限制，照常立即跑。
- 已跑过的交易日记在 `meta/state/scheduler/`；manifest 里已有当天日更记录的也算跑过，
  所以切换到这套机制或手工跑过之后，不会再重复跑一整遍。
- 旧的 `--stale-at` 已停用，改用 `[job.stale] run_at`。

`--check` 比较已安装文件与按主机选择生成的模板，存在差异或缺少任务时退出 1；
它不启动任务，也不检查 launchd 是否已加载。`--dry-run` 只写预览目录。
常规安装包含 daily、stale、events 三个任务；stale 继承 daily 组别并通过
`cne run daily --stale-only --groups ...` 限定补抓范围。现有事件任务的间隔和组别保留；
额外手工安装的事件任务不由此安装器删除。

补抓只重试快照类数据集（资金流、估值、人气榜等只有「当天」的数据），历史类留给下一次日更按日期补；
补抓期间不请求 push2。

日任务的质量错误和实际调度的 `CNE_GATE_GROUPS`（默认 core）滞后会让任务退出 1。
soft 组仍按失败次数升级，但同一日期重试不会重复计为多天。
每周使用 `audit --full --quality-only` 检查质量，freshness 由分组 `status` 单独门禁，
因此未调度的分钟线不会仅因滞后而触发「数据异常」。全湖健康报告仍显示这些滞后。

- 生成 `~/Library/LaunchAgents/com.cnequity.daily.plist`
- 每小时唤醒，**每个交易日北京时间 `[job.daily] run_at`（默认 17:30）之后跑一次**；
  与本机时区、夏令时无关，重装不保留旧的本机时间。
- 非交易日自动跳过（退出 0）
- **漏跑 / 周末补数**：`uv run python scripts/run_catchup.py`（门禁 core + breadth；水位已齐则
  `skipped_already_fresh`），或 `scripts/daily_pipeline.sh YYYY-MM-DD` /
  `CNE_TRADE_DATE=...`（全组定点）
- **按当前出口选择调度组**：先检查源健康，再启用实际能够维护的组。一个出口的失败不能推出某个地域都不可用；出现拒绝先冷却，不通过换出口继续同一轮抓取。见[取数与源保护](fetch-policy.md)。

```bash
launchctl list | grep cnequity
launchctl start com.cnequity.daily   # 手动触发
scripts/uninstall_scheduler.sh
```

**Linux cron**：

```cron
# 每小时唤醒；CNE_SCHEDULED=1 让脚本自己按北京时间 run_at 判断每个交易日只跑一次
7 * * * * CNE_SCHEDULED=1 /path/to/cnequity/scripts/daily_pipeline.sh
37 * * * * CNE_SCHEDULED=1 /path/to/cnequity/scripts/stale_pipeline.sh
# 事件流独立运行，包含周末；按所需频率调整
20 20 * * * /path/to/cnequity/scripts/events_pipeline.sh
```

**Windows 任务计划程序**（原生 Win10/11；`daily_pipeline.sh` 不适用于 PowerShell）：

1. 先确认 `cne doctor` 与 `cne config validate` 通过，`data.root` 用短绝对路径（如 `D:\lake`）。
2. 打开「任务计划程序」→ 创建基本任务 → 每天 16:05（或收盘后任一时刻）。
3. 操作选「启动程序」：

| 字段 | 示例 |
|------|------|
| 程序/脚本 | `C:\path\to\.venv\Scripts\cne.exe` |
| 添加参数 | `run daily --all-groups --config C:\path\to\configs\cnequity.toml` |
| 起始于 | `C:\path\to`（仓库或配置所在目录） |

`run events` 另建每日任务（包括周末）。Windows 任务计划程序和普通 cron 的时间是本机时间；只有使用仓库 scheduler gate 的任务才按配置的北京时间判断。也可在 `.ps1` 中顺序调用并保留失败退出码：

```powershell
$cne = "C:\path\to\.venv\Scripts\cne.exe"
$cfg = "C:\path\to\configs\cnequity.toml"
& $cne run daily --all-groups --config $cfg
$dailyExit = $LASTEXITCODE
& $cne run events --config $cfg
$eventsExit = $LASTEXITCODE
exit ([Math]::Max($dailyExit, $eventsExit))
```

> 控制台中文乱码时：`chcp 65001`，或设置用户环境变量 `PYTHONUTF8=1`。

## 每日 Pipeline

```
core → capital → signals → fundamentals → macro_risk → research
  → health_notify.sh
  → backup_meta.sh
  → group summary（gate vs soft）
```

- 单组失败不中断后续组（尽量多采数据）
- 结尾摘要区分 **gate**（默认 `CNE_GATE_GROUPS=core`）与 **soft**（东财等）
- 默认 `CNE_SOFT_FAIL_OK=1`：gate OK 时 soft 失败 **warn-only、exit 0**（只宽容短暂失败，持续失败会升级）；
  需要任一组失败都阻断时可设 `CNE_SOFT_FAIL_OK=0` 让 soft 失败仍 exit 1
- 东财超时/连接失败不重试（`[sources.eastmoney] timeout_sec`，默认 15s）
- 生产 `daily_pipeline.sh` 常设 `workers=1`（TDX 客户端与多进程兼容性）

组与 step 映射见 [配置 — 调度组](../getting-started/configuration.md#调度组)。

## 日志

目录：`{data.root}/logs/`

| 文件 | 内容 |
|------|------|
| `daily-YYYYMMDD.log` | 各组 cne 输出 |
| `health-YYYYMMDD.log` | audit / status 全文 |
| `launchd.out.log` / `launchd.err.log` | launchd 标准流 |

## 日常巡检命令

```bash
cne status --datasets                 # 新鲜度；STALE 时退出 1
cne audit --full                      # 湖级健康；UNHEALTHY 退出 1
cne stats show                        # 行数概览
cne sources slo --enforce             # 30 日关键源可用性（缺历史也 fail-closed）
cne sources resilience --enforce      # 核心数据集独立备援门禁与单源爆炸半径
cne verify --runs --days 20 --enforce # 连续交易日运行证据
```

## 失败处置

1. 查看 `daily-*.log` 定位失败组
2. 重跑单组：`cne run daily --group <name>`
3. 批级失败：`cne status` → `cne run retry --run-id <id>`
4. 复核：`cne audit --full` + `cne status --datasets`

详见 [故障排查](troubleshooting.md)。

## 服务目标（SLO）

| 指标 | 目标 |
|------|------|
| 日更成功率 | 两周内 ≥99% 交易日 pipeline 退出 0 |
| 告警时效 | 失败当次 run 结束分钟内通知 |
| 新鲜度 | T+1 `status --datasets` 无 STALE（季频数据集按 `max_staleness_days`） |

## 备份与恢复

**元数据备份**：`manifest.db` + `meta/state/` + `meta/quality/` + `meta/revisions/` +
`meta/source_snapshots/` + `meta/source_health/` + `meta/stability/`

**不备份**：`adj_factors_cache`（可 derive 重算）、`locks`、curated parquet（可重采）

```bash
scripts/backup_meta.sh
scripts/backup_meta.sh "" /Volumes/ext/cne-bak 30
```

**恢复**：

```bash
cd data/cnequity/meta
tar -xzf ../backups/meta-YYYYMMDD-HHMMSS.tar.gz
cne status                 # 确认水位恢复
cne run daily --group core # 增量续采
```

默认备份在湖内，磁盘级容灾请将 `CNE_BACKUP_DIR` 指到湖外。

需要冻结可复现实验所依赖的 Parquet 时，使用带校验和、revision receipt、契约指纹和
运行 lineage 的可移植快照：

```bash
cne snapshot create research-20260828 \
  --dataset daily_bars --dataset instruments --dataset trading_status
cne snapshot verify research-20260828
cne snapshot restore research-20260828 /new/empty/cnequity-restore
```

恢复命令只接受新目录或空目录，拒绝活动湖根目录，也不会覆盖已有文件。恢复后对新目录运行
`cne status --datasets` 与研究消费者契约测试，再执行切换。

## 20 个交易日验收

`cne verify --runs` 从权威 `trading_calendar` 取最近窗口，并按 logical trade date 选择最新
`daily:core` attempt。缺 run、核心 stage 失败、或只有旧 `warning` 且没有 dataset receipt，
都会失败；研究/建议层单独降级只有在 receipt 能证明核心无失败时才计为通过。报告写入
`meta/stability/latest.json` 和不可变历史目录。不得手工补写或把日历日当交易日。

## 环境变量

`CNE_CONFIG` 与 `CNE_LOG_DIR` 由 `cne` 本身读取，其余由 [scripts.md](scripts.md) 中的 shell 脚本读取。

| 变量 | 默认 | 作用 |
|------|------|------|
| `CNE_CONFIG` | `configs/cnequity.toml` | 所有命令 `--config` 的默认值（显式 `--config` 优先）。默认值是相对路径，挂 cron 时设这个变量就不必再 `cd` |
| `CNE_LOG_DIR` | `{data.root}/logs` | 日志；长跑的 `cne` 命令也会在这里留一份 |
| `CNE_GROUPS` | 全部 6 组 | 覆盖 pipeline 组列表 |
| `CNE_NOTIFY` | `1` | `0` 关闭通知 |
| `CNE_BACKUP_DIR` | 湖内 backups | 备份目录 |
| `CNE_BACKUP_RETENTION_DAYS` | 14 | 保留天数 |

## 数据湖目录（init 后）

```
{data.root}/
  staging/
  curated/
  derived/
  meta/manifest.db
  meta/quality/
  meta/source_snapshots/
  meta/on_demand/
  duckdb/cnequity.duckdb
```

## 分组 cron 示例

分组模式（`--group`）各组末尾会自动 compact→audit，数据写入 curated：

```cron
# 核心参考 + 行情 + 派生（周一至周五 16:05）
5 16 * * 1-5 cd /path/to/cnequity && cne run daily --group core --config configs/cnequity.toml

# 资金面（16:35）
35 16 * * 1-5 cne run daily --group capital --config configs/cnequity.toml

# 信号类（17:05）
5 17 * * 1-5 cne run daily --group signals --config configs/cnequity.toml
```

生产更推荐用 `scripts/daily_pipeline.sh`（见上文），它会串行跑完全部组并做健康检查与备份。

## 收尾补抓

正常补抓使用独立调度的 `scripts/stale_pipeline.sh`。它在较晚的窗口只处理仍落后的 snapshot 数据集，避免主日更任务内等待造成调度超时；与主任务共享锁，重叠时跳过。`daily_pipeline.sh` 保留 `CNE_STALE_RETRY=1` 兼容开关，但默认关闭。

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `CNE_STALE_RETRY` | `0` | 旧的进程内等待补抓，仅兼容需要时设 `1` |
| `CNE_STALE_RETRY_DELAY_SEC` | `1800` | 仅旧兼容路径生效 |
| `CNE_SOURCE_HEALTH` | `1` | 每日日更后复用真实采集证据，仅主动探测过期或未触达端点；设 `0` 关闭 |
| `CNE_SOURCE_VANTAGE` | `local` | 当前出口的稳定标签；不要把海外样本标成 `cn` |

**为什么需要它。** `snapshot` 数据集（`valuation_metrics`、`fund_flow`、`sector_members`、`analyst_consensus` 等）只抓 run 当天——源端在那一个调度窗口里中断，那天就**永久没了**，后面任何一次 run 都补不回来（重放会伪造行，这是 `fetch_semantics` 的设计）。

这不是重试不够：`clist.py` 的 per-host 重试加退避一直都在，`valuation_metrics` 在 2026-07-30 / 07-31 是把所有 host 的重试都耗尽了。缺的是**当天的第二个窗口**。

**错开窗口是重点。** 立刻重试大概率撞上同一场中断，因此补抓由独立任务在较晚时间发起。

默认 `CNE_SOFT_FAIL_OK=1` 时 soft 组当日失败只告警；连续失败会按配置升级。独立补抓有自己的退出码与日志。旧兼容补抓失败会出现在主任务的 `stale-retry:` 摘要中。

### 中断持续几小时怎么办

独立任务可设置更晚的调度窗口：

```cron
5 20 * * 1-5 cd /path/to/cnequity && scripts/stale_pipeline.sh
```

`--stale-only` 没有落后的数据集时不建 run、直接退出 0，重复挂无害。

配套的可见性：

```bash
cne status --datasets # 有 STALE 退出 1
cne serve             # 面板首屏就列出 STALE 数据集
```

## Init 与资源

```bash
cne init --config configs/cnequity.toml
```

全市场初始化会按标的和源端历史范围分页，耗时及磁盘用量取决于范围、缓存、可达性和失败续跑。先检查配置和空闲空间；单数据集回填另有 `backfill --plan`；正式任务保持单实例运行。macOS/Windows 上 `cne config create` 默认采用保守的 worker 数，修改前先用 `cne config validate` 核对。

## 日内数据（minute_bars / minute_bars_5m）

**默认关闭，不在默认调度里。** 分钟数据的请求数与证券数、源端可用窗口和分页深度一起增长；即使只选一天，全市场也要扫很多证券。落盘量也会随保留频率与日期增长，运行中还需为 staging 和修订留空间。先用少量 `--symbols` 做真实范围验证，并用 `backfill --plan` 核对源开关、窗口与切片。

TDX 分钟线从当前 tip 向过去翻页，因此种子按**标的**分片（`backfill_chunk_symbols=200`）；按日期分片会重复读取较新的页。请求间隔和最大在途数跨进程共享；提高 `fetch_workers` 只可能减少空等，不能提高配置的请求速率，更不等于源方允许该速率。连接或一批证券失败会记入失败范围供续跑，不应通过无限重试或切换出口继续冲击同一源。分钟线建议先做小范围验收，再扩到大范围；实际批次进度和缺口报告比静态耗时表可靠。

### 挂上去

```toml
[minute_bars]
enabled = true
scope = "index:000300.SH" # 或 "watchlist" + symbols，或 "all"
frequencies = ["5m"]      # 5m 是唯一有真历史的频率
fetch_workers = 4
```

```bash
# 一次性种子（分片、可续跑）
cne backfill minute_bars_5m --start 2024-08-01 --end 2026-07-31

# 只拉几只，不改配置（--symbols 会临时覆盖 scope 并开启本次抓取）
cne backfill minute_bars_5m --start 2026-05-01 --end 2026-07-31 \
  --symbols 600519.SH,000001.SZ

# 日更：单独一个 group，不要塞进 core
cne run daily --group intraday
```

越过源端视野的 `--start` 会被直接拒绝并给出可用起点——见 [catalog.md 历史视野](../datasets/catalog.md)。

分钟线验收不能只看最后日期。审计会在已启用的频率和配置证券范围内，用正成交量日线
检查整日缺失，报 `minute_bars_missing_session`；无成交日不作为缺失证据。现有分钟记录
的盘中完整性、时段和量额对照仍单独检查。默认回看 7 个自然日，长窗口恢复应另做全窗口验收。

每次取数的完整失败名单和空返回名单保存在运行 manifest 的
`metrics.stages.<dataset>.source_metrics.tdx_protocol`，字段为 `failed_symbols`、
`empty_symbols`，并附带 `frequency/start/end`。失败会沿用引擎的 warning/degraded 门禁；
空返回只能说明未取到记录，必须与日线或来源停牌证据核实，不能直接认定正常停牌。
整批全空仍判失败，并清除进程缓存的 TDX 主机，使后续重试重新检查可访问性；不在失败点
无限重试。量额对照除市场中位数外，也以原有 0.95..1.05 容差检查个别证券日，报
`minute_bars_daily_outliers`。差异不直接证明分钟源错误，应保留日线与分钟原始证据后裁定。

## 回填完成验收

Init 或首次全量回填 compact + derive 成功且 `cne status` 为 success 后，在同一维护窗口内做下列检查，再挂 cron / 接下游。

### 前置

```bash
cne status --config configs/cnequity.toml # success，failed batch = 0
cne audit  --config configs/cnequity.toml # 无 mock_source / pk_duplicate error
ls data/cnequity/curated/daily_bars/      # 应有 trade_date=YYYY-MM-DD 分区
```

若配置里 `[adj_factors].adjust_types` 只有 `qfq` 而你要用后复权，先追加 `"hfq"` 并重跑
`cne derive adj_factors`。

### 幂等

```bash
.venv/bin/python scripts/accept_backfill.py snapshot \
  --config configs/cnequity.toml --out /tmp/curated-counts.json

cne run daily --config configs/cnequity.toml   # 骨架一趟即可，验收看的是幂等性

.venv/bin/python scripts/accept_backfill.py check \
  --config configs/cnequity.toml --compare /tmp/curated-counts.json
```

核心数据集（`daily_bars`、`instruments`、`adj_factors` 等）行数应与重跑前一致。

### 口径抽查

```bash
.venv/bin/python scripts/accept_backfill.py check \
  --config configs/cnequity.toml \
  --symbol 600519.SH --start 2024-01-01 --end 2024-12-31
```

对照行情软件的未复权 close 与后复权 adj_close（除权日前后各抽一天）。

### 按年覆盖

```bash
.venv/bin/python scripts/accept_backfill.py check --config configs/cnequity.toml
# 看 === daily_bars by year ===
```

正常形态：2016→近年 symbols 缓增，每年 `rows ≈ symbols × ~240` 交易日，无单年腰斩。
若某年明显低于中位数 70%，对该年窗口做 `cne backfill daily_bars` 或 targeted retry。

### 消费层冒烟

```python
from cnequity.query import load

raw = load("daily_bars", start="2024-06-01", end="2024-06-30")
tradable = load(
    "daily_bars",
    start="2024-06-01",
    end="2024-06-30",
    adjust="hfq",
    universe="all_a",
)
assert tradable.height < raw.height
assert "adj_close" in tradable.columns
```

### 验收 checklist

| # | 项 | 通过标准 |
|---|-----|----------|
| 1 | 幂等 | 同窗口重跑后核心数据集 row count 不变 |
| 2 | 口径 | 标杆股 close/adj_close 与行情软件一致（人工） |
| 3 | 覆盖 | 按年行数无异常断崖；2016 起分区连续 |
| 4 | 消费 | `load(..., universe="all_a")` 剔除 ST/停牌；`adj_close` 可算 |
| 5 | 审计 | 最新 run audit 无 error；`source=mock` 行数 = 0 |

## 备源策略

1. 主源失败 → batch 退避重试（最多 3 次）
2. 仍失败 → 标记 batch failed；可选备源写入 `meta/source_snapshots`
3. `cne audit` 对比主源与 snapshot，由人决定是否切源
4. 不要静默用备源覆盖 curated canonical 行

## 逐证券日线覆盖

```bash
cne verify --bars --config configs/cnequity.toml --start 2026-09-07 --end 2026-09-15
```

该命令以配置的 ingest universe 检查证券×交易日，包括整个窗口没有任何行情的证券。
上市前、退市后不要求行情；有明确非交易状态的日期可豁免。供应商返回空数据不构成停牌证明。
JSON 中 `unresolved_keys` 是缺行情/非交易证据的键数，`unknown_listing_symbols` 是无法确定
期望区间的代码；任一未解决时退出 1。它不会补抓或修改行情，也不能由普通水位检查替代。
完整历史检查按 64 个交易日分块。

## 相关文档

- [脚本说明](scripts.md)
- [故障排查](troubleshooting.md)
- [Schema 契约](../datasets/schema.md)
- [逐源限制](../datasets/sources.md)

## 衍生品回填与修复

先运行 `backfill futures_bars/option_bars --plan --exchange ... --start ... --end ...`，核对范围后去掉 `--plan`。缺行使用 `--refresh`；回填会自动更新合约表及派生。遇到拒绝保留持久欠账并停源冷却，不提高并发。部署新版本后重建旧的推断日期及连续合约/Greeks；详细步骤与证据目录见 [衍生品指南](../recipes/derivatives.md)。
