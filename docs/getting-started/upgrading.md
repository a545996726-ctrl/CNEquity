# 升级与兼容性

升级前记录 `cne --version`，阅读[更新日志](../changelog.md)，备份配置与需要长期保留的数据。安装方法见[安装与升级](installation.md)。

## 软件与数据版本

软件版本、数据集 schema 版本与数据 revision 含义不同。软件升级不代表历史数据已迁移；数据修正也不一定改变 schema。

0.x 的次版本可能包含有说明的兼容性变化。字段、单位、主键或历史语义变化应以对应版本的迁移说明为准，不能只依据列名相同继续拼接。

## 升级步骤

1. 核对新版本的范围、已知限制与迁移要求。
2. 更新软件后执行 `cne config upgrade`：把新版本加入的调度 step 和调度组补进本地配置，原文件自动备份，凭证、路径与其他设置不动。想先看改动可加 `--dry-run`；完整差异用 `cne config diff` 查看。
3. 执行版本说明要求的迁移，并核对状态与质量报告。
4. 使用原有查询验证关键数据口径，再恢复定时任务。

迁移说明见仓库的 [contracts/migrations](https://github.com/rootSunc/CNEquity/tree/main/contracts/migrations)，常用工具见[运行脚本](../operations/scripts.md)。回退软件不一定能回退已迁移的数据，恢复时同时核对配置、软件与数据版本。

## 版本清理行为迁移（待发布）

`cne run clean` 改为全部只预览，包括 staging、来源快照、日志与历史版本；无 `--dry-run` 也不标记或删除。现有定时脚本不再释放空间。实际删除字段和 `bytes_freed` 为空或 0，候选大小见 `logical_bytes_selected`。`--force` 只扩大预览范围，显式 `--reconcile-runs` 仍会修改运行状态。

首次标记前须通过 `cne storage import --manifest FILE` 导入经审核的引用清单。可在 serve 的“存储运维”页检查并确认标记，或使用 `storage plan` / `apply --phase mark` 开始观察期。满 7 天只产生到期提示，不能直接删除。

物理删除统一从 `cne serve` 的 `#/storage` 页面检查并确认。CLI 的 `storage apply --phase purge` 及试验 purge 已禁用，`--maintenance-window` 不能绕过网页确认。操作者先停止外部查询、其他服务和采集调度，核对页面清单并勾选两项确认后执行；面板会暂停自身读取，但不会替你停止外部进程。staging、来源快照和日志目前仅报告，未提供网页删除。

不要用旧版本程序执行清理：旧程序不识别新增网页确认边界。升级后重启 serve 才会加载新的路由和保护机制。详细约束见[版本生命周期命令](../reference/cli.md#cne-storage)。

归档原目录也有独立观察期。完整快照携带保留依据和外部依赖声明，恢复后重新绑定引用；涉及生命周期登记的增量包暂时拒绝，应改用完整快照。归档和快照均不会自动解除既有保护。

## 反馈问题

在 [Issues](https://github.com/rootSunc/CNEquity/issues) 提供版本、脱敏配置片段、最小复现和错误信息。不要上传凭证、个人湖、完整运行日志或私人实验记录。安全问题见 [SECURITY.md](https://github.com/rootSunc/CNEquity/blob/main/SECURITY.md)。
