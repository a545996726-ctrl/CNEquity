"""Generate the CLI option/default reference from registered Click commands."""

from __future__ import annotations

import argparse
from pathlib import Path

import click

from cnequity.cli.main import cli


def _leaves(group: click.Group, prefix: str = ""):
    for name, command in sorted(group.commands.items()):
        path = f"{prefix} {name}".strip()
        if isinstance(command, click.Group):
            yield from _leaves(command, path)
        else:
            yield path, command


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _default(param: click.Parameter) -> str:
    value = param.default
    if value.__class__.__name__ == "Sentinel":
        return "—"
    if callable(value):
        return "运行时计算"
    if value is None:
        return "—"
    if isinstance(value, (tuple, list)):
        return ", ".join(map(str, value)) or "空列表"
    return str(value)


def render() -> str:
    lines = [
        "# CLI 参数与默认值",
        "",
        "由 `scripts/sync_cli_options.py` 从 Click 注册表生成。命令用途、副作用与操作场景分别见 [CLI 参考](cli.md) 和 [副作用清单](cli-surface.md)。",
        "",
    ]
    for path, command in _leaves(cli):
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
                f"| `{_cell(label)}` | `{_cell(_default(param))}` | {_cell(description)} |"
            )
        if not command.params:
            lines.append("| — | — | 无参数 |")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / "docs/reference/cli-options.md"
    expected = render()
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            print("CLI options out of sync; run python scripts/sync_cli_options.py")
            return 1
        print("CLI options in sync")
        return 0
    path.write_text(expected, encoding="utf-8")
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
