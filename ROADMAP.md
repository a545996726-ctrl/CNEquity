# Roadmap

CNEquity is a data layer, not a trading strategy or a hosted quote service. Everything below is
ordered around one promise: make reproducible A-share history easy to build, inspect, and consume.

Dates are deliberately absent. This is a personal open-source project, and a roadmap that promises
quarters it cannot keep is worse than one that promises none.

## Now · 0.9

- **Cross-check the lake against a licensed peer.** Daily bars, financial statements, valuations,
  sector bars and corporate actions can now be sourced from a licensed endpoint and used to
  arbitrate disagreements, so a disputed value is switched only when a third source backs it.
- **Make the audit answer harder questions.** Balance sheets must balance, a valuation column
  holding amounts instead of ratios is caught, and the compliance register is checked against what
  `curated` actually holds rather than against its own routing table.
- _Add your next 0.9 themes here._

## Next

- **Publish the MCP server to the official registry.** `cne mcp` already exposes the lake read-only
  through 6 tools; what is missing is the registry metadata and a stable installation contract.
- _Add your next themes here._

## 1.0 criteria

1. The canonical schemas and provenance columns have a documented compatibility policy.
2. A fresh install can complete the demo and diagnose source/network failures clearly.
3. Daily runs are resumable, auditable, and safe to retry after a partial source failure.
4. ~~The supported Python and operating-system matrix is tested in CI.~~ — met in 0.8: Linux,
   macOS and Windows across Python 3.10–3.14.
5. Legal and source-retention limits are documented for every published dataset.

## Explicit non-goals

- A backtesting engine, portfolio optimizer, or trading-signal marketplace.
- Redistributing upstream market data from this repository.
- Hiding source limits behind synthetic rows or silent fallbacks.

## Already shipped

Delivered work lives in the [changelog](CHANGELOG.md), not here — a roadmap that accumulates
completed items stops being a roadmap. Highlights through 0.8: point-in-time query mode, versioned
universe profiles, committed dataset revisions and portable snapshots, machine-readable dataset
contracts, run-level receipts with provenance, a source-use policy register, source SLO and
stability gates, and a `cne run events` job for the feeds that publish 7x24.

## Requests

Feature requests and source additions are welcome when they preserve the data-layer boundary.
Before opening an issue, see [CONTRIBUTING.md](.github/CONTRIBUTING.md), the
[dataset catalog](docs/datasets/catalog.md), and the
[legal notes](docs/legal-and-data-sources.md).
