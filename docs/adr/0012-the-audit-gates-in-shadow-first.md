# ADR 0012: The audit is a publication gate, and it runs in shadow first

- Status: Accepted
- Date: 2026-09-13
- Relates to: [ADR-0003](0003-canonical-curated-with-source-snapshots.md)

## Context

The lake runs 84 quality checks after every load. They produce findings at
three severities — 35 checks can emit `error`, 32 `warning`, 16 `info` — and
write them to `meta/quality/findings/<run_id>.json`.

None of it changed what a run reported. `step_audit` recorded
`status="success"` unconditionally, so a run whose audit found errors still
finished green. The machinery to act on it already existed:
`aggregate_run_status` fails a run when a `core` step fails, and `step_audit`
is registered with `criticality="core"`. The only missing piece was the status
itself.

Two things made that worse than a missing feature.

`step_audit` declares `depends_on=["compact", …]`, so it runs *after* the rows
are in curated. The audit has never been able to prevent anything; at best it
can fail the run that already published.

And the pipeline was built not to escalate. `scripts/daily_pipeline.sh`
defaults `CNE_SOFT_FAIL_OK=1`, which exits 0 when a non-gate group fails, for
a good reason: an overseas host sees expected EastMoney lag, and a daily red
build that is usually wrong gets ignored. `docs/operations/runbook.md:264`
records what that cost — *"更糟的是它很安静… 上面那两天就是这样过了三天没人
发现"*: a soft group down for three days, unnoticed, because each individual
day was warn-only and therefore green.

## Decision

**Make the audit able to fail a run, and default it to shadow mode.**

`[quality].audit_gate` takes `off`, `shadow` (default) or `block`:

- `block` — `step_audit` records `failed` when the audit produced any `error`
  finding, and `aggregate_run_status` fails the run.
- `shadow` — the run still succeeds, but every affected run appends one line
  to `meta/quality/audit_gate.jsonl` with the severity breakdown.
- `off` — pre-existing behaviour.

Only `error` gates. The 32 checks that emit `warning` include known and
accepted source limitations; gating on them would fire every day, which is the
same failure as not gating at all.

**Escalate the pipeline on persistence, not on a single result.**
`CNE_SOFT_FAIL_MAX_DAYS` (default 3) tracks a per-group consecutive-failure
streak under `meta/state/soft_streak/`. A soft group that fails once stays
warn-only; one that fails three days running exits 1. A group that succeeds
clears its streak.

## Consequences

**Positive.** The distinction the runbook incident needed now exists in code:
one bad day is noise, three is an outage. And a gate that nobody has measured
can be measured before it is armed — `audit_gate.jsonl` answers "how often
would this have stopped a run?" with a file rather than an argument.

**Negative.** Shadow mode is a mode that does nothing, and modes that do
nothing get left on. This ADR does not set a date for `block`, because the
honest input is a quarter of `audit_gate.jsonl` that does not exist yet.
Whoever reads it should either arm the gate or turn it `off` and say why;
leaving it in shadow for ever is the failure mode.

**Neutral.** Blocking fails the run; it does not prevent the write. Moving the
audit ahead of `compact` — so bad rows never reach curated — is a larger
change to the step graph and is not attempted here.

## Alternatives considered

**Flip `CNE_SOFT_FAIL_OK` to 0.** The direct reading of the incident. Rejected
because it is wrong for the machine the incident happened on: an overseas host
sees EastMoney lag most days, so the default would produce a red build that is
usually expected, and an alert that is usually wrong is one you stop reading.
The streak counter keeps the quiet day quiet and makes the third one loud.

**Gate on `warning` as well as `error`.** Rejected on the same reasoning at
the finding level: 32 checks emit warnings, several for source limitations
that are documented and permanent.

**Arm the gate immediately in `block`.** Tempting — the checks exist, the
severities exist, and shadow mode delays the benefit. Rejected because nobody
knows how many runs it stops. If the answer is "most of them", arming it
without looking converts a silent-wrongness problem into a
nobody-reads-the-red-build problem, which is where this started.

**Alert instead of gate.** A notifier on error findings, leaving the run
green. Rejected as the status quo with extra steps: the failure recorded in
the runbook was not that nobody was told, it was that nothing changed colour.
