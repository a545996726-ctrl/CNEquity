"""Keep the derivative source capability table tied to the reader registry."""

import argparse
from pathlib import Path

from cnequity.adapters.futures_exchange.registry import capabilities

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs/datasets/sources.md"
START = "<!-- derivative-capabilities:start -->"
END = "<!-- derivative-capabilities:end -->"


def render() -> str:
    lines = [
        START,
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
            "此表由 `scripts/dev/sync_derivative_sources.py` 从读取器注册表生成。起点是适配器路由边界，不证明源端或本湖连续完整；`experimental` 尚未通过真实载荷验收。INE 2018 年期货使用能源中心独立日文件，2019 年起随 SHF 路由合并发布；其独立起点不能套用 SHF 日期。",
            "",
            END,
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = PAGE.read_text()
    if START not in text or END not in text:
        raise SystemExit("source documentation is missing capability markers")
    before, rest = text.split(START, 1)
    _, after = rest.split(END, 1)
    updated = before + render() + after
    if args.check:
        if updated != text:
            print("derivative source docs are stale; run scripts/dev/sync_derivative_sources.py")
            return 1
        print("derivative source docs are in sync")
    else:
        PAGE.write_text(updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
