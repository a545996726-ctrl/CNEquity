"""Regenerate every documentation page that mirrors code, or check they still do.

One entry point for what used to be five scripts. Each target renders the text
its file should hold from the code it mirrors; ``--check`` fails instead of
writing, which is what CI runs.

    python scripts/dev/sync_docs.py                 # rewrite every target
    python scripts/dev/sync_docs.py --check         # CI: fail on any drift
    python scripts/dev/sync_docs.py cli-options     # one target

Targets:

* ``schema`` — the column tables in ``docs/datasets/schema.md`` (synced, not
  generated: the hand-written 说明 cells survive, see below);
* ``config`` — ``configs/cnequity.example.toml`` mirrors the packaged template;
* ``derivatives`` — the capability table in ``docs/datasets/sources.md``;
* ``cli-options`` — ``docs/reference/cli-options.md`` from Click's registry;
* ``cli-surface`` — ``docs/reference/cli-surface.md``, the reviewed side-effect
  inventory; a new command fails until its row is written below;
* ``pypi-readme`` — ``README.pypi.md`` is ``README.md`` with every relative link
  made absolute, since PyPI resolves none of them.

Schema page notes:

The page is not generated. Its prose carries rationale that no schema can hold
— why ``trading_status`` splits ST from suspension, what the volume unit is —
and regenerating it would throw that away. What it cannot maintain by hand is
the mechanical half: 11 of 42 datasets had no section at all, and a column
added in ``domain/schemas.py`` reached the docs only if someone remembered.

So this syncs rather than generates. Column names, order and types come from
``DATASET_SCHEMAS``; the ``说明`` cell is hand-written and is carried across by
column name. A column that leaves the schema loses its row (and its
description) — that is the point.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cnequity.adapters.futures_exchange.registry import capabilities  # noqa: E402
from cnequity.cli.main import cli  # noqa: E402

DOC_PATH = REPO_ROOT / "docs" / "datasets" / "schema.md"

# --- schema ------------------------------------------------------------------

# The page's own vocabulary, not Polars'. Keep the spellings already in use so
# a sync does not rewrite every row the first time it runs.
_TYPE_NAMES = {
    "String": "string",
    "Date": "date",
    "Boolean": "bool",
    "Float64": "float64",
    "Int8": "int8",
    "Int32": "int32",
    "Int64": "int64",
    "Datetime(time_unit='us', time_zone='UTC')": "timestamp",
    "Datetime(time_unit='us', time_zone=None)": "timestamp（naive）",
}

_TABLE_HEADER = "| 列 | 类型 | 说明 |"
_TABLE_DIVIDER = "|--------|------|-------|"
# Datasets that are deliberately documented somewhere else.
_SECTION_ANCHOR = "### Compact 去重"


def _type_name(dtype: object) -> str:
    text = str(dtype)
    return _TYPE_NAMES.get(text, text.lower())


def _schemas() -> dict[str, dict[str, object]]:
    from cnequity.domain.schemas import DATASET_SCHEMAS

    return DATASET_SCHEMAS


def _heading_datasets(heading: str, datasets: list[str]) -> list[str]:
    """Return every dataset a ``####`` heading documents.

    Headings are not always one dataset (``minute_bars / minute_bars_5m``) and
    not always bare (``stock_news（按需缓存）``).
    """
    return [
        name for name in datasets if re.search(rf"(?<![\w_]){re.escape(name)}(?![\w_])", heading)
    ]


def _split_table(lines: list[str], start: int) -> tuple[int, int, dict[str, str]]:
    """Locate the first table after *start*; return its bounds and descriptions."""
    index = start
    while index < len(lines) and not lines[index].startswith("| 列 "):
        if lines[index].startswith("#### "):
            return -1, -1, {}
        index += 1
    if index >= len(lines):
        return -1, -1, {}
    table_start = index
    index += 2  # header + divider
    descriptions: dict[str, str] = {}
    while index < len(lines) and lines[index].startswith("|"):
        cells = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        if cells:
            description = cells[2] if len(cells) > 2 else ""
            # One row often stands for several columns that share a note --
            # "open / high / low / close" or "source / data_version /
            # fetched_at". Expanding those to one row per column must carry the
            # note to each of them, or the sync quietly deletes the sentence
            # that explained the group.
            for name in (part.strip() for part in cells[0].split("/")):
                if name and (name not in descriptions or not descriptions[name]):
                    descriptions[name] = description
        index += 1
    return table_start, index, descriptions


def _render_table(schema: dict[str, object], descriptions: dict[str, str]) -> list[str]:
    rows = [_TABLE_HEADER, _TABLE_DIVIDER]
    for column, dtype in schema.items():
        rows.append(f"| {column} | {_type_name(dtype)} | {descriptions.get(column, '')} |")
    return rows


def sync(text: str) -> tuple[str, list[str]]:
    schemas = _schemas()
    datasets = list(schemas)
    lines = text.split("\n")
    notes: list[str] = []
    documented: set[str] = set()

    index = 0
    while index < len(lines):
        if not lines[index].startswith("#### "):
            index += 1
            continue
        heading = lines[index][5:].strip()
        names = _heading_datasets(heading, datasets)
        if not names:
            index += 1
            continue
        documented.update(names)
        table_start, table_end, descriptions = _split_table(lines, index + 1)
        if table_start < 0:
            notes.append(f"{heading}: no column table found, left alone")
            index += 1
            continue
        rendered = _render_table(schemas[names[0]], descriptions)
        if lines[table_start:table_end] != rendered:
            notes.append(f"{heading}: column table synced")
        lines[table_start:table_end] = rendered
        index = table_start + len(rendered)

    missing = [name for name in datasets if name not in documented]
    if missing:
        anchor = next(
            (i for i, line in enumerate(lines) if line.startswith(_SECTION_ANCHOR)),
            len(lines),
        )
        block: list[str] = []
        for name in missing:
            block.append(f"#### {name}")
            block.append("")
            block.extend(_render_table(schemas[name], {}))
            block.append("")
            notes.append(f"{name}: section added")
        lines[anchor:anchor] = block

    return "\n".join(lines), notes


# --- cli-options -------------------------------------------------------------


def _option_leaves(group: click.Group, prefix: str = ""):
    for name, command in sorted(group.commands.items()):
        path = f"{prefix} {name}".strip()
        if isinstance(command, click.Group):
            yield from _option_leaves(command, path)
        else:
            yield path, command


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _default(param: click.Parameter, ctx: click.Context) -> str:
    value = param.get_default(ctx, call=False)
    if value.__class__.__name__ == "Sentinel":
        return "—"
    if callable(value):
        return "运行时计算"
    if value is None:
        return "—"
    if isinstance(value, (tuple, list)):
        return ", ".join(map(str, value)) or "空列表"
    return str(value)


def render_cli_options() -> str:
    lines = [
        "# CLI 参数与默认值",
        "",
        "由 `scripts/dev/sync_docs.py` 从 Click 注册表生成。命令用途、副作用与操作场景分别见 [CLI 参考](cli.md) 和 [副作用清单](cli-surface.md)。",
        "",
    ]
    for path, command in _option_leaves(cli):
        ctx = click.Context(command)
        lines.extend([f"## `cne {path}`", "", "| 参数 | 默认值 | 说明 |", "|---|---|---|"])
        for param in command.params:
            if isinstance(param, click.Option):
                label = ", ".join([*param.opts, *param.secondary_opts])
                description = param.help or ""
            else:
                label = param.name or ""
                description = "位置参数"
            if param.required:
                description = f"必填；{description}" if description else "必填"
            description = description or "—"
            lines.append(
                f"| `{_cell(label)}` | `{_cell(_default(param, ctx))}` | {_cell(description)} |"
            )
        if not command.params:
            lines.append("| — | — | 无参数 |")
        lines.append("")
    return "\n".join(lines)


# --- cli-surface -------------------------------------------------------------

# Each leaf has one concise user-visible side-effect classification. Adding a
# command fails --check until someone reviews its request and write boundary.
EFFECTS: dict[str, tuple[str, str]] = {
    "audit": ("条件", "读取湖、输出审计；启用外部对照时访问源"),
    "backfill": ("执行时", "补历史并写暂存与发布数据；--plan 仅读配置和状态"),
    "check": ("无", "读取状态、新鲜度、审计结果与统计；统计过期时重算，--full 重跑全湖审计"),
    "config": ("无", "create 写个人配置；upgrade 备份后补调度 step；validate/diff 仅读"),
    "contract diff": ("无", "读取契约并输出差异"),
    "contract show": ("无", "读取契约；指定输出路径时写文件"),
    "contract validate": ("无", "校验契约"),
    "decision-data cash-rights": ("无", "读取湖并输出决策资料"),
    "decision-data payment-gaps": ("无", "读取湖并输出决策资料"),
    "decision-data stock-terms": ("无", "读取湖并输出决策资料"),
    "delisted status": ("无", "读取退市状态"),
    "derive": ("条件", "写派生数据；adj_factors 等模式可访问源"),
    "doctor": ("无", "离线检查配置与环境"),
    "init": ("条件", "sample 离线；demo/quick/full 取数；layout-only 创建目录"),
    "mcp": ("条件", "默认读湖；--live 可取源数据；--http 监听本地端口"),
    "profile list": ("无", "列出内置范围"),
    "profile show": ("无", "显示内置范围"),
    "query": ("条件", "SQL 读湖；按需数据缓存未命中或刷新时取数"),
    "repair corporate-action-gaps": (
        "--apply 时",
        "默认离线预览；--apply 按缺口查询 Baostock 与巨潮资讯，核验后发布新版本并保留旧版本",
    ),
    "repair layout": ("无", "默认只预览；--apply 发布新版本，保留旧版本和重复观察证据"),
    "repair orphan-symbols": (
        "无",
        "默认预览，可能登记旧湖基线版本；--apply 移除无日线证券的因子和公司行为行，发布新版本并保留旧版本",
    ),
    "repair stale-suspensions": (
        "无",
        "默认预览，可能登记旧湖基线版本；--apply 移除成交日线否定的推断停牌，发布新版本并保留旧版本",
    ),
    "repair valuation-basis": ("无", "默认只预览；--apply 发布新版本并保留旧版本"),
    "run clean": ("无", "仅预览过期文件；显式 --reconcile-runs 可修改运行状态"),
    "run compact": ("无", "将已结束、未发布的暂存数据发布到湖"),
    "run daily": ("执行时", "增量取数并写湖；默认含全部调度组与事件流"),
    "run events": ("执行时", "事件取数并写湖"),
    "run retry": ("执行时", "重试失败范围并写湖"),
    "serve": (
        "条件",
        "启动湖面板。回环地址可从操作页发起白名单内的取数与日更；--read-only 关闭写入口；远程取数须 --allow-remote-ops；存储清理仍须网页确认；取数开关经预览确认后写回配置并备份，不因此启动取数",
    ),
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
    "stats show": ("无", "读取本地统计；过期或明细视图需要时自动重算"),
    "status": ("无", "读取运行状态与数据集覆盖"),
    "storage archive": ("无", "复制已登记试验、逐文件校验并登记封存工件；保留源目录"),
    "storage artifact-verify": ("无", "读取并校验封存工件完整内容"),
    "storage experiment-create": ("无", "创建空试验目录和独立实例身份并登记 active"),
    "storage experiment-seal": ("无", "显式结束试验并绑定已验证归档；源内容改变后不能按旧封存清理"),
    "storage experiment-plan": ("无", "校验引用、原目录及归档，保存冗余原位置退出计划"),
    "storage experiment-apply": ("无", "仅原地标记试验；CLI purge 禁用，删除须在 serve 网页确认"),
    "storage resolve": ("无", "校验旧路径对应的归档位置；不重写报告"),
    "storage apply": ("无", "复核版本计划并 mark；CLI purge 禁用，删除须在 serve 网页确认"),
    "storage explain": ("无", "读取版本保留依据"),
    "storage hold": ("无", "增加对象保护并取消待删除标记"),
    "storage import": ("无", "校验并登记引用清单；只增加保护"),
    "storage inspect": ("无", "读取版本、保留原因与登记试验"),
    "storage plan": ("无", "校验保留依赖并保存版本计划；不删除数据"),
    "ths-official backfill": ("执行时", "调用凭证源并补缺"),
    "ths-official capture": ("执行时", "调用凭证源并写对照证据"),
    "ths-official repair-bars": ("执行时", "预演也取数；--apply 写修复"),
    "ths-official resource-sectors": ("执行时", "预演也取数；--apply 写暂存"),
    "verify": ("条件", "默认本地校验；--repair 访问源并修复"),
}


def _surface_leaves(group: click.Group, prefix: str = "") -> list[str]:
    result: list[str] = []
    for name, command in group.commands.items():
        path = f"{prefix} {name}".strip()
        result.extend(
            _surface_leaves(command, path) if isinstance(command, click.Group) else [path]
        )
    return result


def render_cli_surface() -> str:
    actual = set(_surface_leaves(cli))
    declared = set(EFFECTS)
    if actual != declared:
        raise ValueError(
            f"CLI side-effect inventory drift: missing={sorted(actual - declared)}, "
            f"removed={sorted(declared - actual)}"
        )
    rows = [
        "# CLI 命令副作用清单",
        "",
        "由 `scripts/dev/sync_docs.py` 从 Click 命令注册表核对。‘执行时’指运行命令主体；`--help` 不执行。详细源选择与限制见[取数策略](../operations/fetch-policy.md)。",
        "",
        "| 命令 | 第三方取数 | 本地效果 |",
        "|---|---|---|",
    ]
    for path in sorted(actual):
        network, effects = EFFECTS[path]
        rows.append(f"| `cne {path}` | {network} | {effects} |")
    return "\n".join([*rows, ""])


# --- derivatives -------------------------------------------------------------

DERIVATIVE_START = "<!-- derivative-capabilities:start -->"
DERIVATIVE_END = "<!-- derivative-capabilities:end -->"


def render_derivative_table() -> str:
    lines = [
        DERIVATIVE_START,
        "",
        "| 发布者 | 路由 | 期货起点 | 期权起点 | 生命周期参考 | 历史参考 | 状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in capabilities():
        lines.append(
            f"| {row['exchange']} | {row['route']} | {row['futures_since']} | "
            f"{row['options_since'] or '不支持'} | {'有' if row['reference'] else '无'} | "
            f"{'有' if row['reference_history'] else '无'} | {row['status']} |"
        )
    lines.extend(
        [
            "",
            "此表由 `scripts/dev/sync_docs.py` 从读取器注册表生成。起点是适配器路由边界，不证明源端或本湖连续完整；`experimental` 尚未通过真实载荷验收。INE 2018 年期货使用能源中心独立日文件，2019 年起随 SHF 路由合并发布；其独立起点不能套用 SHF 日期。",
            "",
            DERIVATIVE_END,
        ]
    )
    return "\n".join(lines)


# --- pypi-readme -------------------------------------------------------------

SITE_URL = "https://rootsunc.github.io/CNEquity/"
REPO_URL = "https://github.com/rootSunc/CNEquity"
RAW_URL = "https://raw.githubusercontent.com/rootSunc/CNEquity/main/"
_RELATIVE = re.compile(r'(\]\(|src=")(?!https?:|#|mailto:)([^)"\s]+)')


def _absolute(target: str) -> str:
    """Where a README-relative link points once the page is rendered on PyPI."""
    path, _, anchor = target.partition("#")
    suffix = f"#{anchor}" if anchor else ""
    if path.startswith("docs/") and path.endswith(".md"):
        # mkdocs directory URLs: docs/a/b.md -> a/b/, docs/a/README.md -> a/
        page = path.removeprefix("docs/").removesuffix(".md")
        page = page.removesuffix("README").removesuffix("index")
        return SITE_URL + (page.rstrip("/") + "/" if page else "") + suffix
    if path.startswith("docs/assets/"):
        return RAW_URL + path
    return f"{REPO_URL}/blob/main/{path}{suffix}"


def render_pypi_readme() -> str:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    body = _RELATIVE.sub(lambda m: m.group(1) + _absolute(m.group(2)), readme)
    return (
        "<!-- Generated from README.md by scripts/dev/sync_docs.py; edit README.md. -->\n\n" + body
    )


# --- driver ------------------------------------------------------------------


def _schema() -> tuple[Path, str, list[str]]:
    updated, notes = sync(DOC_PATH.read_text(encoding="utf-8"))
    return DOC_PATH, updated, notes


def _config() -> tuple[Path, str, list[str]]:
    source = REPO_ROOT / "src/cnequity/config/templates/cnequity.example.toml"
    return REPO_ROOT / "configs/cnequity.example.toml", source.read_text(encoding="utf-8"), []


def _derivatives() -> tuple[Path, str, list[str]]:
    page = REPO_ROOT / "docs/datasets/sources.md"
    text = page.read_text(encoding="utf-8")
    if DERIVATIVE_START not in text or DERIVATIVE_END not in text:
        raise SystemExit("source documentation is missing capability markers")
    before, rest = text.split(DERIVATIVE_START, 1)
    _, after = rest.split(DERIVATIVE_END, 1)
    return page, before + render_derivative_table() + after, []


def _cli_options() -> tuple[Path, str, list[str]]:
    return REPO_ROOT / "docs/reference/cli-options.md", render_cli_options(), []


def _cli_surface() -> tuple[Path, str, list[str]]:
    return REPO_ROOT / "docs/reference/cli-surface.md", render_cli_surface(), []


def _pypi_readme() -> tuple[Path, str, list[str]]:
    return REPO_ROOT / "README.pypi.md", render_pypi_readme(), []


TARGETS: dict[str, Callable[[], tuple[Path, str, list[str]]]] = {
    "schema": _schema,
    "config": _config,
    "derivatives": _derivatives,
    "cli-options": _cli_options,
    "cli-surface": _cli_surface,
    "pypi-readme": _pypi_readme,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail on drift instead of writing")
    parser.add_argument(
        "targets", nargs="*", metavar="TARGET", help=f"subset ({', '.join(TARGETS)}); default all"
    )
    args = parser.parse_args()
    unknown = sorted(set(args.targets) - set(TARGETS))
    if unknown:
        parser.error(f"unknown target(s): {', '.join(unknown)}")
    stale = 0
    for name in args.targets or TARGETS:
        path, expected, notes = TARGETS[name]()
        current = path.read_bytes() if path.is_file() else None
        if current == expected.encode("utf-8"):
            print(f"{name}: in sync")
            continue
        if args.check:
            stale += 1
            print(f"{name}: OUT OF SYNC — {path.relative_to(REPO_ROOT)}")
            for note in notes:
                print(f"  - {note}")
            continue
        path.write_bytes(expected.encode("utf-8"))
        for note in notes:
            print(f"  - {note}")
        print(f"{name}: wrote {path.relative_to(REPO_ROOT)}")
    if stale:
        print("\nRun: python scripts/dev/sync_docs.py")
    return int(bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
