# ADR 0009: Share dilution is one fact, however two vendors label it

- Status: Accepted
- Date: 2026-09-13
- Relates to: [ADR-0003](0003-canonical-curated-with-source-snapshots.md)

## Context

`corporate_actions` keys on `(symbol, ex_date, action_type)`. That shape is
right for the case it was built for: a company can pay cash and issue shares on
one ex-date, and those are two facts that both matter and that add.

送股 and 转增 are not that case. Both hand the holder more shares and dilute the
price identically; the difference is which account the shares are drawn from —
retained earnings or capital reserve. Vendors do not agree on how to report it,
and under this primary key a disagreement does not compete for a row. It
occupies a second one.

Measured over the full dataset on 2026-09-09, 30 ex-dates hold an EastMoney
`transfer` row carrying exactly the ratio TDX files as `bonus` — one event,
stored twice, and neither row wrong on its own terms. Three more hold EastMoney
at ten times TDX's figure, a 每10股/每股 slip. The unadjusted close settles
those three: 603538.SH, 603585.SH and 688557.SH fell to 1/1.40, 1/1.46 and
1/1.47 of the prior close on 2026-07-09, which is a 送0.4/0.45 dilution and not
an extra 转4.0.

The consequence was quiet. `derive/adj_factors.py` summed `bonus_ratio` and
`transfer_ratio` to form the dilution term, because within one vendor's rows
they genuinely do add. Across vendors it turned a 0.4 dilution into 0.8 and
raised a crosscheck finding against a factor series that was right.

A third reading exists. 同花顺's licensed event stream reports a single
`per_share_bonus`, and measured over 34 comparable transfer events it equals
送股 alone in 33 — it does not carry 转增 at all. Three vendors, three
conventions, one economic event.

## Decision

**`action_type` stays in the primary key.** A dividend and a share issue on one
date are two facts, and collapsing them would lose the distinction that makes
the key useful.

**Share-dilution ratios never sum across sources.** `bonus_ratio` and
`transfer_ratio` add within a vendor's own rows and nowhere else. A consumer
computing dilution aggregates per source first, then takes one source's view of
the event — the largest, since a vendor reporting less has not seen less of it,
it has classified part of it elsewhere.

**The contradiction stays visible rather than being normalised away.**
`corporate_action_classification` reports both shapes: the same ratio filed as
送 by one source and 转 by another, and a ratio that is exactly ten times
another source's. Rewriting one vendor's classification to match another's at
ingest would make the lake look consistent while hiding that its sources are
not, and would destroy the evidence that caught the scale error.

## Consequences

- The dilution term is right for the 30 double-filed ex-dates, and a consumer
  that sums the two columns naively is now wrong in a way a check will name.
- `transfer_ratio` is null on every row from the licensed 同花顺 stream, by
  construction — it reports 送股 only, and folding that into `bonus_ratio`
  would manufacture a caliber it does not publish.
- Anything outside `derive/adj_factors.py` that adds the two columns carries the
  same bug. The check finds the data; it does not find the code.
- Picking "the largest single-source view" is a heuristic, not a proof. It is
  right when one vendor splits an event and another reports it whole, and wrong
  if a vendor ever over-reports. Nothing observed does, and the check would show
  it if one started.

## Alternatives considered

- **Drop `action_type` from the key**, one row per `(symbol, ex_date)` with all
  ratio columns. Genuinely cleaner for this problem, and it would let ADR-0003's
  canonical rule arbitrate the two vendors as it already arbitrates everything
  else. Rejected for cost: a breaking schema change and a rewrite of every
  corporate-action adapter, to fix 33 rows in 73,947.
- **Normalise 转 into 送 at ingest.** One convention, no contradiction, and no
  way to tell afterwards that two sources ever disagreed — which is exactly the
  signal that exposed the ten-times error.
- **Trust the source that reports more.** Would have taken EastMoney's 4.0 over
  TDX's 0.4 on all three scale errors, against what the market price plainly
  says.
