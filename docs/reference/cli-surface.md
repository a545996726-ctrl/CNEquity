# CLI 命令副作用清单

由 `scripts/sync_cli_surface.py` 从 Click 命令注册表核对。‘执行时’指运行命令主体；`--help` 不执行。详细源选择与限制见[取数策略](../operations/fetch-policy.md)。

| 命令 | 第三方取数 | 本地效果 |
|---|---|---|
| `cne audit` | 条件 | 读取湖、输出审计；启用外部对照时访问源 |
| `cne backfill` | 执行时 | 补历史并写暂存与发布数据；--plan 仅读配置和状态 |
| `cne config` | 无 | create 写个人配置；validate/diff 仅读 |
| `cne contract diff` | 无 | 读取契约并输出差异 |
| `cne contract show` | 无 | 读取契约；指定输出路径时写文件 |
| `cne contract validate` | 无 | 校验契约 |
| `cne decision-data cash-rights` | 无 | 读取湖并输出决策资料 |
| `cne decision-data payment-gaps` | 无 | 读取湖并输出决策资料 |
| `cne decision-data stock-terms` | 无 | 读取湖并输出决策资料 |
| `cne delisted backfill` | 执行时 | 补退市历史并写湖 |
| `cne delisted status` | 无 | 读取退市状态 |
| `cne derive` | 条件 | 写派生数据；adj_factors 等模式可访问源 |
| `cne doctor` | 无 | 离线检查配置与环境 |
| `cne init` | 条件 | sample 离线；demo/quick/full 取数；layout-only 创建目录 |
| `cne mcp` | 条件 | 默认读湖；--live 可取源数据 |
| `cne profile list` | 无 | 列出内置范围 |
| `cne profile show` | 无 | 显示内置范围 |
| `cne query` | 条件 | SQL 读湖；按需数据缓存未命中或刷新时取数 |
| `cne run clean` | 无 | 删除符合条件的本地文件；--dry-run 仅预览 |
| `cne run compact` | 无 | 将暂存数据发布到湖 |
| `cne run daily` | 执行时 | 增量取数并写湖 |
| `cne run events` | 执行时 | 事件取数并写湖 |
| `cne run retry` | 执行时 | 重试失败范围并写湖 |
| `cne serve` | 无 | 启动只读本地面板 |
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
| `cne stats show` | 无 | 读取本地统计；可能补建缺失摘要 |
| `cne status` | 无 | 读取运行状态与数据集覆盖 |
| `cne ths-official backfill` | 执行时 | 调用凭证源并补缺 |
| `cne ths-official capture` | 执行时 | 调用凭证源并写对照证据 |
| `cne ths-official repair-bars` | 执行时 | 预演也取数；--apply 写修复 |
| `cne ths-official resource-sectors` | 执行时 | 预演也取数；--apply 写暂存 |
| `cne verify` | 条件 | 默认本地校验；--repair 访问源并修复 |
