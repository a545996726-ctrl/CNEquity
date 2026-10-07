# CLI side-effect inventory

Checked against the Click command registry by `scripts/dev/sync_docs.py`. “When run” means when the command body executes; `--help` executes nothing. For source selection and limits, see [Fetching and source protection](../operations/fetch-policy.md).

| Command | Third-party fetching | Local effects |
|---|---|---|
| `cne audit` | conditional | Reads the lake and prints the audit; accesses sources when external comparison is enabled |
| `cne backfill` | when run | Backfills history, writing staging and published data; --plan only reads config and state |
| `cne check` | none | Reads status, freshness, audit results and stats; recomputes stale stats, --full reruns the full-lake audit |
| `cne config` | none | create writes the personal config; upgrade backs up, then adds schedule steps; validate/diff only read |
| `cne contract diff` | none | Reads contracts and prints the diff |
| `cne contract show` | none | Reads the contract; writes a file when an output path is given |
| `cne contract validate` | none | Validates the contract |
| `cne decision-data cash-rights` | none | Reads the lake and prints decision data |
| `cne decision-data payment-gaps` | none | Reads the lake and prints decision data |
| `cne decision-data stock-terms` | none | Reads the lake and prints decision data |
| `cne delisted status` | none | Reads delisting status |
| `cne derive` | conditional | Writes derived data; modes such as adj_factors may access sources |
| `cne doctor` | none | Checks config and environment offline |
| `cne init` | conditional | sample is offline; demo/quick/full fetch data; layout-only creates directories |
| `cne mcp` | conditional | Reads the lake by default; --live may fetch source data; --http listens on a local port |
| `cne profile list` | none | Lists built-in scopes |
| `cne profile show` | none | Shows a built-in scope |
| `cne query` | conditional | SQL reads the lake; fetches data on an on-demand cache miss or refresh |
| `cne repair corporate-action-gaps` | with --apply | Offline preview by default; --apply queries Baostock and CNINFO per gap, publishes a new version after verification and keeps the old one |
| `cne repair layout` | none | Preview only by default; --apply publishes a new version, keeping the old version and duplicate-observation evidence |
| `cne repair orphan-symbols` | none | Preview by default, may register an old-lake baseline version; --apply removes factor and corporate-action rows of securities without daily bars, publishes a new version and keeps the old one |
| `cne repair stale-suspensions` | none | Preview by default, may register an old-lake baseline version; --apply removes inferred suspensions contradicted by traded daily bars, publishes a new version and keeps the old one |
| `cne repair valuation-basis` | none | Preview only by default; --apply publishes a new version and keeps the old one |
| `cne run clean` | none | Only previews expired files; an explicit --reconcile-runs may modify run state |
| `cne run compact` | none | Publishes finished, unpublished staging data to the lake |
| `cne run daily` | when run | Fetches incrementally and writes the lake; includes all schedule groups and event streams by default |
| `cne run events` | when run | Fetches events and writes the lake |
| `cne run retry` | when run | Retries failed scopes and writes the lake |
| `cne serve` | conditional | Starts the lake dashboard. On a loopback address the operations page can start allowlisted fetches and daily updates; --read-only disables write endpoints; remote fetches require --allow-remote-ops; storage cleanup still needs web confirmation; fetch toggles are written back to the config, with a backup, after preview confirmation and do not start a fetch |
| `cne snapshot create` | none | Creates a local snapshot |
| `cne snapshot delta apply` | none | Applies a local delta package; --dry-run only validates |
| `cne snapshot delta create` | none | Creates a local delta package |
| `cne snapshot delta verify` | none | Verifies a local delta package |
| `cne snapshot export` | none | Exports a local snapshot |
| `cne snapshot import` | none | Imports a local snapshot |
| `cne snapshot restore` | none | Restores a local snapshot to the target directory |
| `cne snapshot verify` | none | Verifies a local snapshot |
| `cne sources limits` | none | Reads egress budgets, cooldowns and local debt; does not probe sources |
| `cne sources policy` | none | Reads source policies |
| `cne sources probe` | conditional | --list is offline; probing accesses sources and writes a report |
| `cne sources resilience` | none | Reads registered sources and probe evidence; may write a report |
| `cne sources slo` | none | Reads probe history and writes stats |
| `cne sources substitutes` | conditional | Reads reports by default; --probe accesses sources |
| `cne stats rebuild` | none | Rebuilds local stats |
| `cne stats show` | none | Reads local stats; recomputes automatically when stale or when a detail view needs it |
| `cne status` | none | Reads run state and dataset coverage |
| `cne storage apply` | none | Rechecks the version plan and marks; CLI purge is disabled, deletion must be confirmed in the serve web UI |
| `cne storage archive` | none | Copies a registered experiment, verifies it file by file and registers the sealed artifact; keeps the source directory |
| `cne storage artifact-verify` | none | Reads and verifies the full contents of a sealed artifact |
| `cne storage experiment-apply` | none | Only marks the experiment in place; CLI purge is disabled, deletion must be confirmed in the serve web UI |
| `cne storage experiment-create` | none | Creates an empty experiment directory with its own instance identity and registers it as active |
| `cne storage experiment-plan` | none | Validates references, the original directory and the archive, and saves an exit plan for the redundant original location |
| `cne storage experiment-seal` | none | Explicitly ends an experiment and binds a verified archive; once source content changes it cannot be cleaned up under the old seal |
| `cne storage explain` | none | Reads the retention reasons for versions |
| `cne storage hold` | none | Adds object protection and clears pending-deletion marks |
| `cne storage import` | none | Validates and registers a reference manifest; only adds protection |
| `cne storage inspect` | none | Reads versions, retention reasons and registered experiments |
| `cne storage plan` | none | Validates retention dependencies and saves a version plan; deletes no data |
| `cne storage resolve` | none | Validates the archive location for an old path; does not rewrite reports |
| `cne ths-official backfill` | when run | Calls the credentialed source and fills gaps |
| `cne ths-official capture` | when run | Calls the credentialed source and writes comparison evidence |
| `cne ths-official repair-bars` | when run | Fetches even in a dry run; --apply writes repairs |
| `cne ths-official resource-sectors` | when run | Fetches even in a dry run; --apply writes staging |
| `cne verify` | conditional | Local validation by default; --repair accesses sources and repairs |
