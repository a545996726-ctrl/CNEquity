# 可选 keyed THS 接入

`ths_official` 是需要账号凭证的可选接口，与 `ths` 公共页面分开配置、限流与记录来源。它不能成为免费核心数据链的强依赖。源登记和数据使用条件见 [来源矩阵](../legal/source-matrix.md)；客户端代码许可证不等于数据再分发许可。

## 配置与入口

通过调用环境设置 `HITHINK_FINANCE_API_KEY`；不要在公共模板、日志或问题报告中写入 Key。个人配置中显式启用 `[sources.ths_official] enabled = true`。`verify` 控制对照证据，`backfill` 控制内容补入，默认后者关闭。

| 命令 | 获取与写入边界 |
|---|---|
| `cne ths-official capture` | 按 `--what` 抓对手源快照；只写快照与运行证据，不替换 canonical |
| `cne ths-official backfill` | 按窗口补财报空缺；支持 `--symbols` 缩小范围，需要内容开关 |
| `cne ths-official repair-bars` | 历史行情核对；默认报告，`--apply` 才写修复结果 |
| `cne ths-official resource-sectors` | 显式板块换源；默认报告，`--apply` 写 staging，之后 compact |

后两项的预演仍会联网并消耗源配额。无 Key、未启用源或未允许相应能力时，命令可能返回 `status=skipped`；这不证明抓到了数据。参数以各命令 `--help` 为准。

## 数据契约

- 财务 `net_profit` 使用归母口径，映射 `parent_holder_net_profit`，不能直接同名拼接包含少数股东的总净利润。
- 披露日要有可追溯的对应证据；当前重述值不能仅因补了日期就升级成原始 PIT。
- 复权事件优先下载全量 dump，再在本地切片；事件流和因子序列结构不同，通过专用仲裁检查比较。
- 送股、转增与配股须遵守 [产品边界](../architecture/overview.md)，不能跨来源叠加同一稀释事实。
- ETF、个股、板块端点的窗口与覆盖不同；适配器会限制已知范围。空响应不能证明退市证券不存在，不能自动删掉原有行。
- 插入缺失主键与替换已有来源是不同操作；修复前核对[产品边界](../architecture/overview.md)和[数据源限制](../datasets/sources.md)。

## 失败处理

鉴权失败先检查凭证和开关；遇到限流或 HTTP 拒绝时等待共享冷却，再做一次小范围诊断。空结果与失败、跳过是不同状态，不能用作覆盖完整的证明。见[取数与源保护](../operations/fetch-policy.md)。
