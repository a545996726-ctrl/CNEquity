from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from cnequity.adapters.cninfo.share_structure_notices import review_share_change_notice

_TEXT = """股票代码：600900  股票简称：长江电力
发行结果暨股本变动公告
本次发行股份购买资产和配套募集资金新增股份已于 2016 年 4
月 13 日在中国证券登记结算有限责任公司上海分公司办理了登记托
管手续。
本次发行前后公司股本结构变动情况如下表所示：
单位：股 变动前 变动数 变动后
有限售条件的流通股合计 6,754,058,520 5,500,000,000 12,254,058,520
无限售条件流通股合计 9,745,941,480 - 9,745,941,480
股份总额 16,500,000,000 5,500,000,000 22,000,000,000
"""


def _review(text=_TEXT, *, vendor=None, code="600900.SH", day=date(2016, 4, 13)):
    return review_share_change_notice(
        text,
        symbol=code,
        change_date=day,
        source_document_id="1202181940",
        source_sha256="a" * 64,
        source_published_at=datetime(2016, 4, 14, 16, tzinfo=timezone.utc),
        vendor_row=vendor
        or {
            "symbol": "600900.SH",
            "change_date": date(2016, 4, 13),
            "total_shares": 22_000_000_000.0,
            "float_shares": 9_745_941_480.0,
            "restricted_shares": 12_254_058_520.0,
            "free_float_shares": 4_617_770_708.0,
        },
    )


def test_original_share_table_proves_only_three_printed_fields():
    reviewed = _review()
    assert reviewed["verified_fields"] == {
        "total_shares": 22_000_000_000,
        "float_shares": 9_745_941_480,
        "restricted_shares": 12_254_058_520,
    }
    assert reviewed["unverified_fields"] == ["free_float_shares"]
    assert reviewed["source_published_at"] == "2016-04-14T16:00:00+00:00"


@pytest.mark.parametrize(
    "text",
    [
        _TEXT.replace("600900", "600901"),
        _TEXT.replace("4\n月 13 日", "4\n月 14 日"),
        _TEXT.replace("22,000,000,000", "22,000,000,001"),
        _TEXT.replace("9,745,941,480 - 9,745,941,480", "9,745,941,480 - 9,745,941,481"),
    ],
)
def test_conflicting_original_facts_are_not_promoted(text):
    assert _review(text) is None


def test_vendor_mismatch_or_naive_publication_fails():
    vendor = {
        "symbol": "600900.SH",
        "change_date": date(2016, 4, 13),
        "total_shares": 21_000_000_000.0,
        "float_shares": 9_745_941_480.0,
        "restricted_shares": 12_254_058_520.0,
    }
    assert _review(vendor=vendor) is None
    with pytest.raises(ValueError, match="timezone-aware"):
        review_share_change_notice(
            _TEXT,
            symbol="600900.SH",
            change_date=date(2016, 4, 13),
            source_document_id="1202181940",
            source_sha256="a" * 64,
            source_published_at=datetime(2016, 4, 15),
            vendor_row=vendor,
        )
