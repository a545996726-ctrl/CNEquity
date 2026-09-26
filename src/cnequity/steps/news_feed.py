"""One incremental fast-news fetch shared by ``news_headlines`` and ``flash_news_wire``.

Both datasets are views of the same EastMoney 7×24 list. They used to page it
separately — twice per 15-minute events run, the whole day each time. Now the
first of the two steps in a run fetches everything published since the older
of the two datasets' newest items (less a small overlap); the other reuses it.
Each dataset archives the same exact page bytes under its own name, so both
keep a replayable wire observation.
"""

from __future__ import annotations

import base64
import logging
import threading
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import polars as pl

from cnequity.config import Config

logger = logging.getLogger(__name__)
CST = ZoneInfo("Asia/Shanghai")
# Items keep their news_id, so re-reading a few minutes costs nothing but a
# duplicate the compact collapses; it guards against items that appear late.
_OVERLAP = timedelta(minutes=30)
_LOCK = threading.Lock()
_CACHE: dict[str, tuple[pl.DataFrame, list[dict], bool]] = {}


def _newest_held(config: Config, dataset: str) -> datetime | None:
    from cnequity.query.parquet_scan import dataset_has_parquet, scan_parquet_root

    root = config.curated_root / dataset
    if not dataset_has_parquet(root):
        return None
    newest = (
        scan_parquet_root(root, partition_col="publish_date")
        .select(
            (
                pl.col("publish_date").cast(pl.Date).cast(pl.Utf8)
                + pl.lit("T")
                + pl.col("publish_time").cast(pl.Utf8)
            ).max()
        )
        .collect()
        .item()
    )
    return datetime.fromisoformat(newest).replace(tzinfo=CST) if newest else None


def since_for(config: Config, trade_date: date) -> datetime:
    """Where the next fetch starts: the older of both datasets' newest items."""
    held = [_newest_held(config, ds) for ds in ("news_headlines", "flash_news_wire")]
    if any(value is None for value in held):
        # A dataset that has nothing yet starts from the run day, as before.
        return datetime.combine(trade_date, time(0, 0), CST)
    return min(held) - _OVERLAP  # type: ignore[type-var]


def shared_fetch(
    config: Config, trade_date: date, run_id: str
) -> tuple[pl.DataFrame, list[dict], bool]:
    """The run's fetch, made once; (rows, raw pages, reached *since*)."""
    from cnequity.adapters.eastmoney.news_feed import fetch_news_since

    with _LOCK:
        cached = _CACHE.get(run_id)
        if cached is None:
            since = since_for(config, trade_date)
            pages: list[dict] = []
            rows, reached = fetch_news_since(since, config=config, recorder=pages)
            if not reached:
                logger.warning(
                    "fast news: the API stopped before %s; anything older was not served",
                    since.isoformat(timespec="minutes"),
                )
            logger.info(
                "fast news: %d item(s) since %s in %d page(s)",
                rows.height,
                since.isoformat(timespec="minutes"),
                len(pages),
            )
            cached = (rows, pages, reached)
            _CACHE.clear()  # one run at a time per process; keep only this one
            _CACHE[run_id] = cached
        return cached


class _Replayed:
    def __init__(self, page: dict) -> None:
        self.content = base64.b64decode(page["content_b64"])
        self.url = page["url"]
        self.status_code = page["status"]
        self.headers = {"content-type": page.get("content_type") or "application/json"}


def _archive(config: Config, dataset: str, run_id: str, scope: str, pages: list[dict]) -> None:
    from cnequity.adapters.eastmoney.raw import archive_response, configured_archive

    archive = configured_archive(config, dataset, run_id=run_id, request_scope=scope)
    for page in pages:
        archive_response(
            archive,
            dataset,
            _Replayed(page),
            request_params={**page["params"], "page": page["page"]},
            run_id=run_id,
            url=page["url"],
            observation_id=f"{run_id or 'anonymous'}:{dataset}:{page['page']}",
            pagination={"page": page["page"], "sort_end": page["sort_end"]},
            captured_at=datetime.fromisoformat(page["captured_at"]),
        )


def stage_news(config: Config, trade_date: date, run_id: str, dataset: str) -> dict:
    """Write *dataset*'s rows from the run's shared fast-news fetch."""
    from cnequity.adapters.eastmoney.news_wire import flash_rows_from_news
    from cnequity.query.parquet_scan import dataset_has_parquet
    from cnequity.steps.http_common import verify_raw_archive, write_fetched

    if not config.sources.get("eastmoney", True):
        raise RuntimeError(f"{dataset}: eastmoney source disabled in config")
    rows, pages, reached = shared_fetch(config, trade_date, run_id)
    scope = f"snapshot:{trade_date.isoformat()}"
    frame = rows if dataset == "news_headlines" else flash_rows_from_news(rows)
    # Archived even when empty: a quiet window is still an observed response.
    _archive(config, dataset, run_id, scope, pages)
    if frame.is_empty():
        if dataset == "flash_news_wire" and not dataset_has_parquet(config.curated_root / dataset):
            # A first capture that finds nothing must not look like a clean start
            # (an empty success once left the dataset unregistered in curated).
            raise RuntimeError(f"{dataset}: no fast news returned and the lake holds none")
        return {"rows_read": 0, "rows_written": 0, "pages": len(pages)}
    evidence = (
        verify_raw_archive(config, dataset, run_id, source="eastmoney", request_scope=scope)
        if config.should_archive_raw(dataset)
        else None
    )
    result = write_fetched(
        config, run_id, dataset, frame, source="eastmoney", raw_archive_evidence=evidence
    )
    result["pages"] = len(pages)
    result["publish_dates"] = sorted({d.isoformat() for d in frame["publish_date"].to_list()})
    if not reached:
        result.setdefault("context_updates", {})["audit_findings"] = [
            {
                "dataset": dataset,
                "severity": "warning",
                "check": "news_gap_beyond_source",
                "message": (
                    f"{dataset}: the fast-news API stopped paging before the lake's "
                    "newest item; items older than what it served are missing"
                ),
            }
        ]
    return result
