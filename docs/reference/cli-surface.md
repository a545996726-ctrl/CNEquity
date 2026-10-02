# CLI 命令副作用清单

由 `scripts/dev/sync_cli_surface.py` 从 Click 命令注册表核对。‘执行时’指运行命令主体；`--help` 不执行。详细源选择与限制见[取数策略](../operations/fetch-policy.md)。

| 命令 | 第三方取数 | 本地效果 |
|---|---|---|
| `cne audit` | 条件 | 读取湖、输出审计；启用外部对照时访问源 |
| `cne backfill` | 执行时 | 补历史并写暂存与发布数据；--plan 仅读配置和状态 |
| `cne check` | 无 | 读取状态、新鲜度、审计结果与统计；统计过期时重算，--full 重跑全湖审计 |
| `cne config` | 无 | create 写个人配置；upgrade 备份后补调度 step；validate/diff 仅读 |
| `cne contract diff` | 无 | 读取契约并输出差异 |
| `cne contract show` | 无 | 读取契约；指定输出路径时写文件 |
| `cne contract validate` | 无 | 校验契约 |
| `cne decision-data cash-rights` | 无 | 读取湖并输出决策资料 |
| `cne decision-data payment-gaps` | 无 | 读取湖并输出决策资料 |
| `cne decision-data stock-terms` | 无 | 读取湖并输出决策资料 |
| `cne delisted status` | 无 | 读取退市状态 |
| `cne derive` | 条件 | 写派生数据；adj_factors 等模式可访问源 |
| `cne doctor` | 无 | 离线检查配置与环境 |
| `cne init` | 条件 | sample 离线；demo/quick/full 取数；layout-only 创建目录 |
| `cne mcp` | 条件 | 默认读湖；--live 可取源数据 |
| `cne profile list` | 无 | 列出内置范围 |
| `cne profile show` | 无 | 显示内置范围 |
| `cne query` | 条件 | SQL 读湖；按需数据缓存未命中或刷新时取数 |
| `cne repair corporate-action-gaps` | --apply 时 | 默认离线预览；--apply 按缺口查询 Baostock 与巨潮资讯，核验后发布新版本并保留旧版本 |
| `cne repair layout` | 无 | 默认只预览；--apply 发布新版本，保留旧版本和重复观察证据 |
| `cne repair orphan-symbols` | 无 | 默认预览，可能登记旧湖基线版本；--apply 移除无日线证券的因子和公司行为行，发布新版本并保留旧版本 |
| `cne repair stale-suspensions` | 无 | 默认预览，可能登记旧湖基线版本；--apply 移除成交日线否定的推断停牌，发布新版本并保留旧版本 |
| `cne repair valuation-basis` | 无 | 默认只预览；--apply 发布新版本并保留旧版本 |
| `cne run clean` | 无 | 仅预览过期文件；显式 --reconcile-runs 可修改运行状态 |
| `cne run compact` | 无 | 将已结束、未发布的暂存数据发布到湖 |
| `cne run daily` | 执行时 | 增量取数并写湖；默认含全部调度组与事件流 |
| `cne run events` | 执行时 | 事件取数并写湖 |
| `cne run retry` | 执行时 | 重试失败范围并写湖 |
| `cne serve` | 无 | 启动湖面板；存储页检查、确认后可标记或删除历史数据 |
| `cne snapshot create` | 无 | 创建本地快照 |
| `cne snapshot delta apply` | 无 | 应用本地增量包；--dry-run 仅校验 |
| `cne snapshot delta create` | 无 | 创建本地增量包 |
| `cne snapshot delta verify` | 无 | 校验本地增量包 |
| `cne snapshot export` | 无 | 导出本地快照 |
| `cne snapshot import` | 无 | 导入本地快照 |
| `cne snapshot restore` | 无 | 恢复本地快照到目标目录 |
| `cne snapshot verify` | 无 | 校验本地快照 |
| `cne sources limits` | 无 | 读取出口预算、冷却及本地欠账；不探测源 |
| `cne sources policy` | 无 | 读取源政策 |
| `cne sources probe` | 条件 | --list 离线；探测时访问源并写报告 |
| `cne sources resilience` | 无 | 读取登记源与探测证据；可写报告 |
| `cne sources slo` | 无 | 读取探测历史并写统计 |
| `cne sources substitutes` | 条件 | 默认读报告；--probe 访问源 |
| `cne stats rebuild` | 无 | 重建本地统计 |
| `cne stats show` | 无 | 读取本地统计；过期或明细视图需要时自动重算 |
| `cne status` | 无 | 读取运行状态与数据集覆盖 |
| `cne storage apply` | 无 | 复核版本计划并 mark；CLI purge 禁用，删除须在 serve 网页确认 |
| `cne storage archive` | 无 | 复制已登记试验、逐文件校验并登记封存工件；保留源目录 |
| `cne storage artifact-verify` | 无 | 读取并校验封存工件完整内容 |
| `cne storage experiment-apply` | 无 | 仅原地标记试验；CLI purge 禁用，删除须在 serve 网页确认 |
| `cne storage experiment-create` | 无 | 创建空试验目录和独立实例身份并登记 active |
| `cne storage experiment-plan` | 无 | 校验引用、原目录及归档，保存冗余原位置退出计划 |
| `cne storage experiment-seal` | 无 | 显式结束试验并绑定已验证归档；源内容改变后不能按旧封存清理 |
| `cne storage explain` | 无 | 读取版本保留依据 |
| `cne storage hold` | 无 | 增加对象保护并取消待删除标记 |
| `cne storage import` | 无 | 校验并登记引用清单；只增加保护 |
| `cne storage inspect` | 无 | 读取版本、保留原因与登记试验 |
| `cne storage plan` | 无 | 校验保留依赖并保存版本计划；不删除数据 |
| `cne storage resolve` | 无 | 校验旧路径对应的归档位置；不重写报告 |
| `cne ths-official backfill` | 执行时 | 调用凭证源并补缺 |
| `cne ths-official capture` | 执行时 | 调用凭证源并写对照证据 |
| `cne ths-official repair-bars` | 执行时 | 预演也取数；--apply 写修复 |
| `cne ths-official resource-sectors` | 执行时 | 预演也取数；--apply 写暂存 |
| `cne verify` | 条件 | 默认本地校验；--repair 访问源并修复 |
