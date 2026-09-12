"""The adjustment-factor dump parser and the arbitration it enables.

Column names come from a dump downloaded 2026-09-09; the caliber limits are
measured facts, so they are asserted rather than assumed.
"""

from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from cnequity.adapters.ths_official.corporate_actions import (
    ThsOfficialDumpError,
    parse_adjustment_factor_dump,
)

CST = timezone(timedelta(hours=8))


def _ms(value: date) -> int:
    return int(datetime(value.year, value.month, value.day, tzinfo=CST).timestamp() * 1000)


def _dump(tmp_path, rows):
    path = tmp_path / "dump.parquet"
    pl.DataFrame(
        rows,
        schema={
            "thscode": pl.Utf8,
            "ticker": pl.Utf8,
            "ex_date_ms": pl.Int64,
            "dividend_per_share": pl.Float64,
            "per_share_bonus": pl.Float64,
            "allotment_ratio": pl.Float64,
            "allotment_price": pl.Float64,
            "currency": pl.Utf8,
        },
    ).write_parquet(path)
    return path


def _row(symbol="600519.SH", ex=date(2024, 6, 20), div=0.0, bonus=0.0, allot=None, price=None):
    return {
        "thscode": symbol,
        "ticker": symbol[:6],
        "ex_date_ms": _ms(ex),
        "dividend_per_share": div,
        "per_share_bonus": bonus,
        "allotment_ratio": allot,
        "allotment_price": price,
        "currency": "CNY",
    }


def test_one_dated_event_expands_to_one_row_per_action_type(tmp_path):
    out = parse_adjustment_factor_dump(_dump(tmp_path, [_row(div=2.5, bonus=0.4)]))
    assert set(out["action_type"].to_list()) == {"cash_dividend", "bonus"}
    by_type = dict(zip(out["action_type"].to_list(), out["cash_dividend"].to_list(), strict=True))
    assert by_type["cash_dividend"] == pytest.approx(2.5)


def test_transfer_ratio_is_never_inferred(tmp_path):
    """`per_share_bonus` is 送股 alone — 33 of 34 comparable events said so.

    Folding it into a transfer would manufacture a caliber the upstream does not
    report, and would over-state dilution on every plain bonus issue.
    """
    out = parse_adjustment_factor_dump(_dump(tmp_path, [_row(bonus=0.4)]))
    assert out["transfer_ratio"].null_count() == out.height
    assert out.filter(pl.col("action_type") == "bonus")["bonus_ratio"].to_list() == [0.4]


def test_allotment_survives_because_only_the_dump_carries_it(tmp_path):
    """The REST event stream has no allotment fields; the lake holds 1,164 events."""
    out = parse_adjustment_factor_dump(_dump(tmp_path, [_row(allot=0.3, price=5.5)]))
    row = out.filter(pl.col("action_type") == "allotment").to_dicts()[0]
    assert row["allotment_ratio"] == pytest.approx(0.3)
    assert row["allotment_price"] == pytest.approx(5.5)


def test_zero_amount_events_produce_no_rows(tmp_path):
    assert parse_adjustment_factor_dump(_dump(tmp_path, [_row()])).is_empty()


def test_ex_dates_are_read_in_shanghai_time(tmp_path):
    out = parse_adjustment_factor_dump(_dump(tmp_path, [_row(ex=date(2024, 1, 2), div=1.0)]))
    assert out["ex_date"].to_list() == [date(2024, 1, 2)]


def test_a_dump_missing_documented_columns_is_refused(tmp_path):
    path = tmp_path / "bad.parquet"
    pl.DataFrame({"thscode": ["600519.SH"]}).write_parquet(path)
    with pytest.raises(ThsOfficialDumpError, match="missing columns"):
        parse_adjustment_factor_dump(path)


def test_arbitration_is_silent_without_a_peer_snapshot(tmp_path):
    """A lake with no key keeps its existing sources and gains no findings."""
    from cnequity.config import Config
    from cnequity.quality.cross_checks import adj_factor_arbitration_findings

    assert adj_factor_arbitration_findings(Config(data_root=tmp_path)) == []
