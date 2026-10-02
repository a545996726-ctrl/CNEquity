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
    """修复已存数据：默认只预览，--apply 核验后发布并保留旧版本。"""


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


@repair.command("corporate-action-gaps")
@config_option
@click.option("--apply", is_flag=True, help="向 Baostock 补取并发布新版本；默认只离线输出计划。")
def corporate_action_gaps(config_path: str, apply: bool):
    """补上新浪与 Baostock 因子都有台阶、湖里却没有记录的除权事件。

    \b
    依据最近一次 `cne derive adj_factor_source` 的证据。附近 10 天内已有、但日期错开的
    记录，若其条款能解释该台阶，就移到台阶日；其余按证券和年份向 Baostock 取分红，
    只收除权日正是台阶日、且条款能解释台阶的行。解释不了的保持缺口，不会凭空补。
    """
    from cnequity.storage.corporate_action_gap_repair import repair_corporate_action_gaps

    _emit(repair_corporate_action_gaps(_cfg(config_path), apply=apply))


@repair.command("orphan-symbols")
@config_option
@click.option("--apply", is_flag=True, help="核验后发布新版本；默认只输出计划。")
def orphan_symbols(config_path: str, apply: bool):
    """删除 daily_bars 里从未出现的证券在 adj_factors 与 corporate_actions 中的行。

    \b
    这类行没有可复权的价格：早年作为净值序列误入的场外基金（519xxx）行情已清理，
    因子和分红却留了下来；未采集行情的上市基金也在其列。它们只会被报成因子与公司行为矛盾。
    按数据集各发布一个新版本，旧版本保留，可按版本读取。
    """
    from cnequity.storage.orphan_symbol_repair import repair_orphan_symbols

    _emit(repair_orphan_symbols(_cfg(config_path), apply=apply))


@repair.command("stale-suspensions")
@config_option
@click.option("--apply", is_flag=True, help="核验后发布新版本；默认只输出计划。")
def stale_suspensions(config_path: str, apply: bool):
    """删除已被实际成交日线否定的推断停牌（trading_status 中 derived_bar_gap 来源的行）。

    \b
    这类行由缺失的日线推断而来；某次运行漏抓了行情时，缺口会被误记为停牌。
    行情补齐后，同一天有成交的日线即证明该行错误。独立来源的停牌记录不受影响。
    """
    from cnequity.storage.orphan_symbol_repair import repair_stale_derived_suspensions

    _emit(repair_stale_derived_suspensions(_cfg(config_path), apply=apply))
