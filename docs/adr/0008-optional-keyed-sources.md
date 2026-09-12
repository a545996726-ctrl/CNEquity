# ADR 0008: Optional keyed sources never own data

- Status: Accepted
- Date: 2026-09-13
- Relates to: [ADR-0003](0003-canonical-curated-with-source-snapshots.md), [ADR-0005](0005-source-routing-vs-switching.md)

## Context

Every source the lake had until now was reachable by anyone who cloned it. A
keyed source is different: it is available to whoever registered for it and
absent for everyone else, and that asymmetry reaches further than it first
appears.

`ths_official` made the question concrete. It closed a nine-year hole in
`balance` and `cashflow`, gave `adj_factors` its first peer in 19,088,826 rows,
and moved 4,398,523 rows of deep history off an unregistered scraper. All of it
useful, and none of it available to a lake without a key.

Three ways that could go wrong:

- A dataset whose **primary** is keyed stops updating for anyone without the
  key. The daily run does not degrade, it fails.
- One switch for "check my data against a licensed peer" and "rewrite nine
  years of it" makes the safe thing imply the dangerous one.
- Quality checks written as peer comparisons go **silent** without a key, so the
  lake that most needs a second opinion is the one that gets none.

The third is the subtle one. Five defects surfaced during this integration — NUL
padding in instrument names, delistings inferred for securities that had not
listed, a transfer ratio double-counted across sources and another at ten times
scale, open-end funds classified as exchange-traded, and a valuation column
holding amounts rather than ratios. Each was *found* because an outside source
disagreed or refused. Had each also been *detected* that way, a keyless lake
would carry all five forever.

## Decision

**A keyed source never owns a row.** `ths_official` may appear as
`backup_source`, as `backfill_source`, and in failover or audit configuration.
It is never a `primary_source`. A lake with no key keeps exactly the sources it
had, and the daily run is unaffected.

**Verification and content are separate switches.** `[sources.X].verify`
defaults on and permits only writes to `meta/source_snapshots` and findings;
`[sources.X].backfill` defaults off and is required before anything reaches
curated. Holding a credential, enabling a source, and letting it change data are
three decisions, not one.

**Content changes still obey ADR-0005.** Filling primary keys that hold no rows
is routing and needs no extra ceremony. Replacing rows that already have an
owner is switching: an explicit command, a dry run by default, and — where an
independent third source exists — only for the rows that source backs.

**Prefer an invariant to a peer.** When a defect can be stated as a property of
the data alone, the check is written that way and runs everywhere. Reserve
peer comparison for what genuinely needs a third party: which of two
self-consistent sources is right.

That last rule is what keeps a keyless lake whole. Of the five defects above,
all five turned out to be expressible as invariants — assets equal liabilities
plus equity; a delisting cannot precede a listing; a ratio is not eight digits;
a quoted price has volume behind it; one event is not two rows from two sources.
The peer found them; the invariant catches them.

## Consequences

- A lake without a key keeps every structural check and loses only the three
  that arbitrate between sources — `adj_factor_arbitration`,
  `daily_bars_arbitration`, `financial_statement_peer`. Those are silent rather
  than failing, because an unavailable second opinion is not evidence of bad
  data.
- Two lakes can legitimately hold different rows. That is visible rather than
  hidden: backfilled rows carry `source='ths_official'`, and the dataset
  catalogue states which coverage windows need a key.
- A keyed source can never become load-bearing by accident. It also cannot
  become load-bearing on purpose without amending this record.
- Writing an invariant is harder than writing a comparison. It is also the only
  version that helps the lake that cannot afford the second source.

## Alternatives considered

- **Let a keyed source be primary where it is better.** It is better in places —
  the official history is licensed where the scraper is not. But a lake that
  fails its daily run for want of a credential is a worse lake than one with a
  scraper in it.
- **One switch per source.** Simpler to configure and wrong in the direction
  that matters: the cautious user who enables a cross-check would be enabling a
  rewrite.
- **Ship the peer comparisons and skip the invariants.** Faster, and it would
  have left every one of the five defects in place for any lake without a key —
  including the 436,533 NAV rows that a volume test finds with no help at all.
