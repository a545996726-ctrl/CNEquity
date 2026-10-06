import os
from datetime import date, datetime, timedelta

import polars as pl

from cnequity.config import Config
from cnequity.derive.minute_resample import (
    derive_minute_resample,
    resample_session,
    stale_sessions,
)
from cnequity.storage.state import StateStore

OLD, NEW = date(2026, 9, 14), date(2026, 9, 15)


def _bars(symbol: str, day: date, step: int, count: int, volume: int = 100) -> pl.DataFrame:
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=9, minutes=30 + step)
    return pl.DataFrame(
        {
            "symbol": [symbol] * count,
            "trade_date": [day] * count,
            "bar_time": [start + timedelta(minutes=i * step) for i in range(count)],
            "frequency": [f"{step}m"] * count,
            **{k: [10.0] * count for k in ["open", "high", "low", "close"]},
            "volume": [volume] * count,
            "amount": [volume * 10.0] * count,
            "source": ["tdx_protocol"] * count,
            "data_version": ["v1"] * count,
            "fetched_at": [datetime(2026, 9, 16)] * count,
        }
    ).with_columns(pl.col("fetched_at").dt.replace_time_zone("UTC"))


def _write(config: Config, dataset: str, day: date, frame: pl.DataFrame) -> None:
    part = config.curated_root / dataset / f"trade_date={day.isoformat()}"
    part.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(part / "part-000.parquet")


def _lake(tmp_path) -> Config:
    config = Config(data_root=tmp_path / "data")
    _write(config, "minute_bars_5m", OLD, _bars("603869.SH", OLD, 5, 6))
    _write(config, "minute_bars_5m", NEW, _bars("603869.SH", NEW, 5, 6))
    _write(config, "minute_bars", NEW, _bars("603869.SH", NEW, 1, 30, volume=7))
    return config


def _age(path, seconds: float) -> None:
    for file in path.rglob("*.parquet"):
        stat = file.stat()
        os.utime(file, (stat.st_atime - seconds, stat.st_mtime - seconds))


def test_session_skips_incomplete_symbol_days_without_falling_back():
    halted = _bars("000016.SZ", NEW, 1, 14, volume=0)
    bars, skipped = resample_session(
        pl.concat([_bars("603869.SH", NEW, 1, 15), halted]),
        pl.concat([_bars("603869.SH", NEW, 5, 3), _bars("000016.SZ", NEW, 5, 3)]),
        "15m",
    )
    assert bars.select("symbol", "resampled_from").rows() == [("603869.SH", "1m")]
    assert skipped.rows() == [("000016.SZ", NEW, "1m")]


def test_derive_writes_each_session_from_its_best_input(tmp_path):
    config = _lake(tmp_path)
    summary = derive_minute_resample(config, "minute_bars_30m")
    assert summary["sessions"] == 2
    assert summary["symbol_days_from_1m"] == 1
    assert summary["symbol_days_from_5m"] == 1
    out = pl.read_parquet(config.derived_root / "minute_bars_30m" / "**" / "*.parquet")
    assert out.sort("trade_date").select("trade_date", "resampled_from", "volume").rows() == [
        (OLD, "5m", 600),
        (NEW, "1m", 210),
    ]
    assert set(out["frequency"]) == {"30m"}
    assert set(out["source"]) == {"derived"}
    # Unscheduled, so no watermark that could only ever read as stale.
    assert StateStore(config.meta_root).get_date("minute_bars_30m") is None


def test_later_one_minute_backfill_makes_the_session_stale(tmp_path):
    config = _lake(tmp_path)
    derive_minute_resample(config, "minute_bars_15m")
    assert stale_sessions(config, "minute_bars_15m") == []
    assert derive_minute_resample(config, "minute_bars_15m")["rows"] == 0
    _age(config.derived_root / "minute_bars_15m" / f"trade_date={OLD.isoformat()}", 60)
    _write(config, "minute_bars", OLD, _bars("603869.SH", OLD, 1, 30))
    assert stale_sessions(config, "minute_bars_15m") == [OLD]
    summary = derive_minute_resample(config, "minute_bars_15m")
    assert summary["sessions"] == 1
    assert summary["symbol_days_from_1m"] == 1


def test_explicit_window_rebuilds_only_that_window(tmp_path):
    config = _lake(tmp_path)
    derive_minute_resample(config, "minute_bars_15m")
    summary = derive_minute_resample(config, "minute_bars_15m", start=OLD, end=OLD)
    assert (summary["sessions"], summary["first"], summary["last"]) == (1, str(OLD), str(OLD))


def test_sessions_without_a_full_interval_are_counted_not_written(tmp_path):
    config = _lake(tmp_path)
    summary = derive_minute_resample(config, "minute_bars_60m", full=True)
    assert summary["rows"] == 0
    assert (summary["skipped_incomplete_1m"], summary["skipped_incomplete_5m"]) == (1, 1)
    assert not (config.derived_root / "minute_bars_60m").exists()


def _cli_config(tmp_path) -> str:
    from cnequity.config.bootstrap import path_for_toml

    path = tmp_path / "cne.toml"
    path.write_text(f'[data]\nroot = "{path_for_toml(tmp_path / "data")}"\n')
    return str(path)


def test_cli_derive_publishes_a_readable_dataset(tmp_path):
    from click.testing import CliRunner

    from cnequity.cli.main import cli
    from cnequity.query import load

    _lake(tmp_path)
    cfg_path = _cli_config(tmp_path)
    result = CliRunner().invoke(cli, ["derive", "minute_bars_15m", "--config", cfg_path])
    assert result.exit_code == 0, result.output
    assert '"symbol_days_from_1m": 1' in result.output
    frame = load("minute_bars_15m", data_root=tmp_path / "data")
    assert frame.sort("trade_date")["resampled_from"].to_list() == ["5m", "5m", "1m", "1m"]


def test_cli_derive_without_minute_input_says_what_to_fetch(tmp_path):
    from click.testing import CliRunner

    from cnequity.cli.main import cli

    cfg_path = _cli_config(tmp_path)
    result = CliRunner().invoke(cli, ["derive", "minute_bars_30m", "--config", cfg_path])
    assert result.exit_code != 0
    assert "minute_bars_5m" in str(result.exception) + result.output
