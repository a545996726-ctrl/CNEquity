# Architecture Decision Records

ADRs in this directory are written in **English** (decision records and
changelog stay English; user-facing docs are Chinese).

Copy [0000-template.md](0000-template.md) for a new record. Number sequentially.

| ADR | Title |
|-----|-------|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions |
| [0002](0002-parquet-lake-over-database.md) | Parquet lake over database |
| [0003](0003-canonical-curated-with-source-snapshots.md) | Canonical curated + source snapshots |
| [0004](0004-store-hfq-derive-qfq-at-query.md) | Store hfq, derive qfq at query |
| [0005](0005-source-routing-vs-switching.md) | Source routing vs switching |
| [0006](0006-publishers-over-vendors.md) | Publishers over vendors, where a publisher exists |
| [0007](0007-two-facts-two-columns-in-trading-status.md) | Two facts, two columns, in trading_status |
| [0008](0008-optional-keyed-sources.md) | Optional keyed sources never own data |
| [0009](0009-share-dilution-is-one-fact.md) | Share dilution is one fact, however two vendors label it |
| [0010](0010-bounded-generation-retention.md) | Committed generations are retained by count, not forever |
| [0011](0011-bitemporal-columns-are-carried-not-required.md) | Bitemporal columns are carried through validation, not added to the schema |
| [0012](0012-the-audit-gates-in-shadow-first.md) | The audit is a publication gate, and it runs in shadow first |
