"""Continuous futures: rolls decided on T-1, forward only, factors continuous."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl

from cnequity.derive.futures_continuous import compute_futures_continuous

D = [date(2026, 3, 2) + timedelta(days=i) for i in range(5)]


def _bars(table: dict[str, list[tuple[float, int]]]) -> pl.DataFrame:
    rows = []
    for symbol, points in table.items():
        for day, (price, oi) in zip(D, points, strict=True):
            rows.append(
                {
                    "symbol": symbol,
                    "exchange": "SHF",
                    "product": "CU",
                    "trade_date": day,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "settle": price,
                    "volume": 10,
                    "open_interest": oi,
                }
            )
    return pl.DataFrame(rows)


def _main(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.filter(pl.col("series") == "main").sort("trade_date")


def test_the_roll_uses_the_previous_close_not_the_same_session():
    bars = _bars(
        {
            "CU2605.SHF": [(100, 50), (100, 50), (100, 50), (100, 50), (100, 50)],
            # Out-holds the front from D[2]'s close, so it is main from D[3].
            "CU2606.SHF": [(110, 10), (110, 10), (110, 90), (110, 90), (110, 90)],
        }
    )
    main = _main(compute_futures_continuous(bars))
    by_day = dict(zip(main["trade_date"], main["contract_symbol"], strict=True))
    assert by_day[D[2]] == "CU2605.SHF"
    assert by_day[D[3]] == "CU2606.SHF"
    assert main.filter(pl.col("trade_date") == D[3])["rolled"].item() is True


def test_adjusted_prices_are_continuous_across_the_roll():
    bars = _bars(
        {
            "CU2605.SHF": [(100, 50)] * 5,
            "CU2606.SHF": [(110, 10), (110, 10), (110, 90), (110, 90), (110, 90)],
        }
    )
    main = _main(compute_futures_continuous(bars))
    before = main.filter(pl.col("trade_date") == D[2]).row(0, named=True)
    after = main.filter(pl.col("trade_date") == D[3]).row(0, named=True)
    assert after["settle"] * after["adj_ratio"] == before["settle"] * before["adj_ratio"]
    assert after["settle"] + after["adj_diff"] == before["settle"] + before["adj_diff"]


def test_rolls_never_go_back_to_an_earlier_month():
    bars = _bars(
        {
            "CU2605.SHF": [(100, 10), (100, 10), (100, 99), (100, 99), (100, 99)],
            "CU2606.SHF": [(110, 50)] * 5,
        }
    )
    main = _main(compute_futures_continuous(bars))
    assert set(main["contract_symbol"]) == {"CU2606.SHF"}


def test_a_contract_about_to_expire_hands_over_even_with_more_interest():
    bars = _bars({"CU2603.SHF": [(100, 90)] * 5, "CU2604.SHF": [(105, 10)] * 5})
    main = _main(
        compute_futures_continuous(
            bars, ends={"CU2603.SHF": date(2026, 3, 16), "CU2604.SHF": date(2026, 4, 15)}
        )
    )
    # Five calendar days before 2026-03-16 is 03-11: still eligible on D[4]=03-06.
    assert set(main["contract_symbol"]) == {"CU2603.SHF"}
    main = _main(compute_futures_continuous(bars, ends={"CU2603.SHF": date(2026, 3, 8)}))
    assert main.filter(pl.col("trade_date") == D[4])["contract_symbol"].item() == "CU2604.SHF"


def test_roll_yield_is_positive_in_backwardation():
    bars = _bars({"CU2605.SHF": [(110, 90)] * 5, "CU2606.SHF": [(100, 10)] * 5})
    main = _main(compute_futures_continuous(bars))
    assert (main["roll_yield"] > 0).all()
