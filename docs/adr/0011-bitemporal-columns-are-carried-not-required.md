# ADR 0011: Bitemporal columns are carried through validation, not added to the schema

- Status: Accepted
- Date: 2026-09-13
- Relates to: [ADR-0003](0003-canonical-curated-with-source-snapshots.md), [ADR-0010](0010-bounded-generation-retention.md)

## Context

`domain/pit.py` defines four bitemporal storage columns — `available_at`,
`source_published_at`, `observed_at`, `revision_id` — and states that they are
"intentionally optional storage columns … not added to `DATASET_SCHEMAS`'
required shape until every writer can provide them". `contracts/v0.8.1.json`
publishes them under `pit_contract.storage_columns` with their dtypes.

None of them had ever reached disk. Across `financial_statement_items`,
`announcement_index`, `valuation_metrics` and `corporate_actions`, every
curated parquet file had zero of the four.

The cause was not a missing writer. `validate_dataframe` ends with
`df.with_columns(casts).select(columns)` where `columns = list(schema)`: it
projects to exactly the registered schema, so any writer that produced the
columns had them dropped before the write. `query/reader.py` compensated by
splitting them out before validation and `hstack`ing them back afterwards —
a workaround whose existence was the evidence.

So the read path re-derived them on every read instead. `revision_id` is a
SHA-256 over each row's business fields, computed in a Python loop at
3.5 µs/row whatever the column already held:

| | |
|---|---|
| one `financial_statement_items` partition (158k rows) | 0.55 s |
| the full dataset (5.5M rows) | ~19 s |
| `top_holders` (4.9M rows) | ~17 s |

Every point-in-time read paid that, to reproduce values that are deterministic.

## Decision

**Carry the columns through validation rather than registering them.**
`validate_dataframe` keeps the four columns when the frame already has them,
casting to the declared dtypes, for datasets in `PIT_DATASET_NAMES` only. It
still never manufactures them: a frame that arrives without them is unchanged,
so this is a passthrough, not an injection. `DATASET_SCHEMAS` and the
per-dataset contract are untouched, which keeps the change non-breaking.

Compaction materialises the columns on both sides of its business-digest
comparison, and a partition written before they existed is rewritten once.
Reading a migrated partition costs 0.003 s instead of 0.55 s.

Two consequences had to be decided with it.

**`observed_at` is excluded from the business digest, alongside `fetched_at`.**
It is `fetched_at` under its bitemporal name. Counting it would make every
reconciliation re-fetch look like a business change, rewriting the partition
and minting a revision on every run — and each revision copies the whole
dataset (ADR-0010). The physical no-op that keeps the lake from growing is
load-bearing, and persisting `observed_at` naively would have destroyed it.

**`revision_id` is a 96-bit (24 hex character) truncated SHA-256.** It is
stored on every row of every PIT dataset, so its width is a storage decision,
not a cryptographic one. Across the current 12.2M PIT rows the chance of any
collision is ~1e-15, and ~1e-13 at a hundred times that volume. The full
64-character form would have added 389 MB to 190 MB of curated parquet;
24 characters add 73 MB. Nothing had persisted the old width, so there was no
migration — and that free window was the reason to decide it immediately
rather than later.

## Consequences

**Positive.** The declared contract is now true: the columns exist on disk.
PIT reads stop re-hashing, and `reader.py` loses its `hstack` workaround.

**Negative.** PIT datasets grow ~2.4× per partition, the bulk of it the
digest. Those bytes are also copied into each committed generation, so
retention (ADR-0010) matters more than before. Migration is lazy — a partition
gains the columns the next time it is compacted — so a lake has mixed files
for as long as it has cold partitions, and the read path must keep its
normalisation fallback indefinitely.

**Neutral.** `available_at` and `source_published_at` are written, but null:
no adapter supplies them yet. They are `unknown`, and `classify_pit_rows`
already treats null as unknown rather than as permission.

## Alternatives considered

**Add the four columns to `DATASET_SCHEMAS`.** The obvious reading of "until
every writer can provide them" — make them required once they are. Rejected:
it changes the published per-dataset contract for five datasets, and the
required-column validation would then reject every existing parquet file until
a full rewrite completed. Passthrough gets the same bytes on disk with a lazy
migration and no contract change.

**Keep deriving on read, and only make the derivation faster.** A vectorised
digest would cut the 19 s substantially without storing anything. Rejected as
insufficient on its own: the derived `revision_id` is stable but the two
source timestamps can never be derived, so the contract stays false however
fast the hashing is. The short-circuit that skips derivation when the column
is already populated was implemented anyway, and is what makes the migrated
read 180× faster rather than merely cheaper.

**Store `revision_id` at full width.** Rejected on measurement: 389 MB against
73 MB, multiplied by retained generations, to move a collision probability
from 1e-15 to 0.

## Known naming debt

`pit_quality` is not what it appears to be. It falls back to the literal
`"strict"` for any dataset that makes no point-in-time claim, so 29 of the 42
registered datasets are published as `strict` — `daily_bars` among them —
while `announcement_index` is the only genuine one. The contract is not wrong:
it also publishes `pit: false`, `pit_grade: "none"` and an empty
`pit_storage_columns` for those datasets, so the answer is available. But a
consumer reading `pit_quality` alone gets the strongest possible claim for a
table nobody has considered.

Fixing the vocabulary (a fourth value, `not_applicable`) changes
`pit_quality` on 29 datasets and `cne contract diff` classifies every one as
breaking. It therefore belongs to a versioned release, not a patch. Until
then `docs/datasets/contract.md` documents the trap and names `pit` as the
field to read.
