"""Offline repairs of stored data, each published as a new retained revision."""

from __future__ import annotations

import json

import click

from cnequity.cli._root import cli
from cnequity.cli._shared import _cfg, config_option


def _emit(value: dict) -> None:
    click.echo(json.dumps(value, indent=2, ensure_ascii=False, default=str))


@cli.group()
def repair():
    """离线修复已存数据：默认只预览，--apply 发布新版本并保留旧版本。"""


@repair.command("layout")
@click.argument("dataset")
@config_option
@click.option("--apply", is_flag=True, help="写入并发布新版本；默认只输出计划。")
def layout(dataset: str, config_path: str, apply: bool):
    """把分区数据集根目录或错误键目录里的文件并入登记分区。

    \b
    同一主键的多份观察只在非空值完全一致时互补填空；有冲突的键按规范规则整行取一份。
    重复键的全部原始观察写入 _quarantine 作为证据，旧版本继续保留。
    """
    from cnequity.storage.layout_repair import LayoutRepairError, repair_layout

    try:
        _emit(repair_layout(_cfg(config_path), dataset.lower(), apply=apply))
    except LayoutRepairError as exc:
        raise click.ClickException(str(exc)) from exc


@repair.command("valuation-basis")
@config_option
@click.option("--apply", is_flag=True, help="写入并发布新版本；默认只输出计划。")
def valuation_basis(config_path: str, apply: bool):
    """统一 valuation_metrics 的市盈率与市值口径。

    \b
    push2 动态市盈率移入 pe_dynamic；Baostock 流通市值由成交均价口径换算为收盘价口径，
    无法核对的保留原值并标注 vwap 口径；总市值按 share_structure 当日有效总股本重建，
    无股本记录的保留年末股本估算并标注。已有口径的行不重算。
    """
    from cnequity.storage.valuation_repair import repair_valuation_basis

    _emit(repair_valuation_basis(_cfg(config_path), apply=apply))
