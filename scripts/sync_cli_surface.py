"""Keep the public command side-effect inventory aligned with Click's registry."""

from __future__ import annotations

import argparse
from pathlib import Path

import click

from cnequity.cli.main import cli

# Each leaf has one concise user-visible side-effect classification. Adding a
# command fails --check until someone reviews its request and write boundary.
EFFECTS: dict[str, tuple[str, str]] = {
    "audit": ("条件", "读取湖、输出审计；启用外部对照时访问源"),
    "backfill": ("执行时", "补历史并写暂存与发布数据；--plan 仅读配置和状态"),
    "config": ("无", "create 写个人配置；validate/diff 仅读"),
    "contract diff": ("无", "读取契约并输出差异"),
    "contract show": ("无", "读取契约；指定输出路径时写文件"),
    "contract validate": ("无", "校验契约"),
    "decision-data cash-rights": ("无", "读取湖并输出决策资料"),
    "decision-data payment-gaps": ("无", "读取湖并输出决策资料"),
    "decision-data stock-terms": ("无", "读取湖并输出决策资料"),
    "delisted backfill": ("执行时", "补退市历史并写湖"),
    "delisted status": ("无", "读取退市状态"),
    "derive": ("条件", "写派生数据；adj_factors 等模式可访问源"),
    "doctor": ("无", "离线检查配置与环境"),
    "init": ("条件", "sample 离线；demo/quick/full 取数；layout-only 创建目录"),
    "mcp": ("条件", "默认读湖；--live 可取源数据"),
    "profile list": ("无", "列出内置范围"),
    "profile show": ("无", "显示内置范围"),
    "query": ("条件", "SQL 读湖；按需数据缓存未命中或刷新时取数"),
    "run clean": ("无", "删除符合条件的本地文件；--dry-run 仅预览"),
    "run compact": ("无", "将暂存数据发布到湖"),
    "run daily": ("执行时", "增量取数并写湖"),
    "run events": ("执行时", "事件取数并写湖"),
    "run retry": ("执行时", "重试失败范围并写湖"),
    "serve": ("无", "启动只读本地面板"),
    "snapshot create": ("无", "创建本地快照"),
    "snapshot delta apply": ("无", "应用本地增量包；--dry-run 仅校验"),
    "snapshot delta create": ("无", "创建本地增量包"),
    "snapshot delta verify": ("无", "校验本地增量包"),
    "snapshot export": ("无", "导出本地快照"),
    "snapshot import": ("无", "导入本地快照"),
    "snapshot restore": ("无", "恢复本地快照到目标目录"),
    "snapshot verify": ("无", "校验本地快照"),
    "sources limits": ("无", "读取出口预算、冷却及本地欠账；不探测源"),
    "sources policy": ("无", "读取源政策"),
    "sources probe": ("条件", "--list 离线；探测时访问源并写报告"),
    "sources resilience": ("无", "读取登记源与探测证据；可写报告"),
    "sources slo": ("无", "读取探测历史并写统计"),
    "sources substitutes": ("条件", "默认读报告；--probe 访问源"),
    "stats rebuild": ("无", "重建本地统计"),
    "stats show": ("无", "读取本地统计；可能补建缺失摘要"),
    "status": ("无", "读取运行状态与数据集覆盖"),
    "ths-official backfill": ("执行时", "调用凭证源并补缺"),
    "ths-official capture": ("执行时", "调用凭证源并写对照证据"),
    "ths-official repair-bars": ("执行时", "预演也取数；--apply 写修复"),
    "ths-official resource-sectors": ("执行时", "预演也取数；--apply 写暂存"),
    "verify": ("条件", "默认本地校验；--repair 访问源并修复"),
}


def _leaves(group: click.Group, prefix: str = "") -> list[str]:
    result: list[str] = []
    for name, command in group.commands.items():
        path = f"{prefix} {name}".strip()
        result.extend(_leaves(command, path) if isinstance(command, click.Group) else [path])
    return result


def render() -> str:
    actual = set(_leaves(cli))
    declared = set(EFFECTS)
    if actual != declared:
        raise ValueError(
            f"CLI side-effect inventory drift: missing={sorted(actual - declared)}, "
            f"removed={sorted(declared - actual)}"
        )
    rows = [
        "# CLI 命令副作用清单",
        "",
        "由 `scripts/sync_cli_surface.py` 从 Click 命令注册表核对。‘执行时’指运行命令主体；`--help` 不执行。详细源选择与限制见[取数策略](../operations/fetch-policy.md)。",
        "",
        "| 命令 | 第三方取数 | 本地效果 |",
        "|---|---|---|",
    ]
    for path in sorted(actual):
        network, effects = EFFECTS[path]
        rows.append(f"| `cne {path}` | {network} | {effects} |")
    return "\n".join([*rows, ""])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "docs" / "reference" / "cli-surface.md"
    expected = render()
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            print("CLI side-effect docs out of sync; run python scripts/sync_cli_surface.py")
            return 1
        print("CLI side-effect docs in sync")
        return 0
    path.write_text(expected, encoding="utf-8")
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
