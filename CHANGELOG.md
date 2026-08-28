# Changelog

## [Unreleased]

### Added

- Repo scaffold: FastAPI (uvicorn) backend serving a Vite/React frontend as
  one process, with a `hmac`/`secrets`-based shared-secret login issuing a
  signed httpOnly session cookie. Binds only to a configured Tailscale
  address (never `0.0.0.0`); no TLS termination in-app. Systemd unit at
  `deploy/signal-deck.service`. (#1)
- Shared adapter base (`sources/base.py`) defining the internal data model
  (`Trade`, `EquityPoint`, `RunStatus`, `HealthSample`, per-slot
  `WinRate`/`PnL`/`Fills`, per-symbol matched-price series with trade
  markers, per-channel latency percentile series) and incremental
  byte-offset log tailing with a pluggable line-decoder seam that
  auto-detects a magic header for future per-line AEAD decryption
  (unimplemented). `sources/rustle.py` adapts rustle's JSONL trade/latency
  logs onto this model. (#2)
- `sources/ticktrader.py` adapts TickTrader-para's logs onto the shared
  model: `TickTraderTradeLogAdapter` reads a per-slot `trade_log.csv`
  (columns looked up by header name, tolerant of column set/order changes
  across strategy versions) for trades, matched prices, and PnL; hand
  `TickTraderLatencyAdapter` a `data_latency.jsonl`/`api_latency.jsonl` and
  a channel name and it emits a running mean/p99/p999 per new sample. (#3)
- Unified run list: `GET /api/runs` discovers runs for both projects by
  scanning a configured root directory per project (`SIGNAL_DECK_RUSTLE_RUNS_DIR`
  / `SIGNAL_DECK_TICKTRADER_RUNS_DIR`), one subdirectory per run holding a
  `run.json` manifest plus that project's log files, and totals each run's
  latest per-slot PnL via the shared adapters. The run list UI polls this
  endpoint every 5s and shows, per run: project, a live/dead heartbeat
  pulse, a status badge (live/stopped/crashed/backtest), elapsed duration
  (or a real start/end date-range for backtests), and PnL — all without a
  full page reload. (#4)
- Run detail Overview tab: clicking a run in the list opens its detail view
  with a live-updating equity curve and recent trade tape. Backend:
  `GET /api/runs/{project}/{run_id}/overview` returns a snapshot (from an
  in-memory cache for completed runs, computed once since their log never
  grows again); `GET /api/runs/{project}/{run_id}/stream` is an SSE feed of
  equity/trade deltas for live runs only, backed by an asyncio poll loop
  (~1s) that tails each currently-live run and fans deltas out to subscribers,
  closing the stream when a run ends. A run whose log is detected as
  encrypted (magic-header sniff — no key-resolution mechanism exists yet) is
  neither polled nor parsed; the Overview shows a "🔒 encrypted — no key
  configured" placeholder while the run's real status (e.g. LIVE) still
  displays normally. (#5)
- Run detail Performance, Market, and Latency tabs, extending #5's ingestion
  pipeline: `GET /api/runs/{project}/{run_id}/overview` and the SSE stream now
  also carry per-slot PnL/win-rate/fill-count (Performance, plus an overall
  total), per-symbol matched price movement with buy/sell trade markers
  (Market), per-component health status, and per-channel ws/api latency
  mean/p99/p999 with an uncapped trend history spanning the run's lifetime
  (Latency). TickTrader-para's latency channels are tailed from their own
  `{channel}_latency.jsonl` files, so the Latency tab (and health) keeps
  working even when a run's trade log is encrypted-locked; rustle interleaves
  everything in one `events.jsonl`, so an encrypted rustle run has no
  separable latency/health to show. Performance and Market show the same
  "🔒 encrypted" placeholder as Overview when locked; the frontend now reads
  a `live_tracked` field from the Overview response to decide whether to open
  the SSE stream, rather than re-deriving that from project name. The Latency
  tab is hidden entirely for backtest runs. (#6)
- Comparison view: a "Compare runs" toggle on the run list lets an operator
  select 2-4 runs, in any mix of project and status, and view them side by
  side — reusing the existing per-run `overview` endpoint rather than adding
  a new one. Shows a table of PnL/fills/win-rate per run (with a PnL delta
  column when exactly 2 runs are selected), an overlaid cumulative-PnL chart
  normalized by run-progress fraction (index/length, not wall-clock time or
  point count, so runs of different lengths still line up start-to-finish),
  and grouped per-slot PnL/fill bar charts. Per-slot bars align slots
  positionally (1st vs. 1st, 2nd vs. 2nd, ...) across runs rather than by
  slot name, since two runs can use unrelated slot identifiers even when
  their counts match; when slot counts differ, a plain fallback message
  replaces the charts instead of a misleading alignment. (#7)
- Config discovery, the backend/UI groundwork for the New Run flow (#9): a
  local JSON settings store (`SIGNAL_DECK_CONFIG_ROOTS_FILE`) holds a list of
  root directories to scan per project, with `GET`/`POST /api/config-roots/{project}`
  to read and add roots and `GET /api/config-scan/{project}` doing a
  recursive `**/*.toml` scan of them on demand. A "Config roots" panel in the
  UI lets an operator add a new root directory per project and preview a
  scan — no upload, catalog, or validation wizard. (#8)
- New Run flow: a two-stage "New run" panel — Stage 1 picks Project → Run
  type → Config (from #8's scan list), Stage 2 (revealed once a config is
  picked) requires an explicit arm toggle before Start is enabled for LIVE,
  or shows a plain, always-enabled "Start backtest" button with a "reads
  historical data only" note for BACKTEST. Backend: `POST /api/runs`
  (`process_control.py`'s `ProcessRegistry.start_run`) rejects any `config`
  path that isn't under one of the project's own registered scan roots
  (#8), then `subprocess.Popen`s the project's configured binary
  (`SIGNAL_DECK_RUSTLE_BINARY` / `SIGNAL_DECK_TICKTRADER_BINARY`) with
  `--config <path>`, redirects stdout/stderr to a per-run log file, writes
  the run's `run.json` manifest immediately (so it shows up in the existing
  run list within one poll, no page reload), and tracks PID/start time in a
  registry persisted to `SIGNAL_DECK_PROCESS_REGISTRY_FILE` that survives a
  dashboard restart. `GET /api/runs` now also reconciles tracked runs'
  liveness (PID polling) on the same 5s cadence the frontend already polls
  it at, updating a finished run's manifest to `stopped` or `crashed`. (#9)
- Stop control: a "Stop run" button on a live run's detail view, with an
  inline "Stop this run? [Confirm stop] [Cancel]" step before it fires.
  Backend: `POST /api/runs/{project}/{run_id}/stop` sends **SIGTERM** (not
  SIGINT — see ADR-0002) to the tracked PID, deduplicated so a repeated stop
  request against the same run sends at most one SIGTERM regardless of
  timing, while a durable "operator stopped run `<name>` at `<time>`" record
  (`SIGNAL_DECK_STOP_LOG_FILE`) is still appended on every request for
  accountability. No halt/cancel/flatten commands, and none of the old
  Control channel's ceremony (`CommandBinding`, proposer/approver, audited
  journal), are exposed or rebuilt. (#10)
- Independent run tracking: `runs.py` discovers runs the dashboard didn't
  launch itself, alongside the existing manifest-based discovery — any
  directory under a project's runs root holding a trade log
  (`trade_log.jsonl`/`.csv`) with no `run.json` sibling, found via a
  depth-agnostic scan (rustle nests two levels deep, ticktrader one). A
  manifest-less run's `run_id` is its directory path relative to the runs
  root with `/` replaced by `__`, stable across dashboard restarts. Liveness
  comes from a `runner.pid` file both engines now write next to their trade
  log (rustle's own `tt-live-runner` change, see its `CHANGELOG.md`;
  ticktrader already had this): present and alive → `live`; otherwise →
  a new `unknown` status, distinct from `crashed`/`stopped` since a run this
  process never launched has no exit code or stop record to tell them apart.
  The Stop button is hidden for `unknown` runs (no ownership to act on), and
  its run-list badge reads as a neutral dot rather than a live pulse or a
  crash indicator. Manifest-less discovery only surfaces a run from today or
  still alive (a cheap mtime/pid check, before any log parsing) — a project's
  runs root can hold months of completed sessions with multi-hundred-MB trade
  logs each, and re-parsing all of them for PnL on every `/api/runs` poll
  (~5s cadence) is a real production-scale problem this project hit directly
  while testing against actual TickTrader-para/rustle log directories.
  Ticktrader's own multi-strategy launch convention (mirrored from its
  dashboard's `reader.py`) writes one "main" session dir per launch — its
  own `trade_log.csv` is header-only — plus one `{main}-{strategy}` sibling
  dir per strategy slot holding the real trades; those siblings are grouped
  under the main dir (summing pnl across the family) so a multi-strategy
  launch shows as one run-list entry instead of one per strategy. The same
  grouping now also applies to that run's detail view (Overview/Performance/
  Market/`/trades`): `live.py` tails every child's `trade_log.csv` instead of
  the main dir's own (empty) one and merges their trades/pnl/fills/prices,
  since slot ids are already strategy-namespaced in that data
  (`comeback_v9_5_0` vs `spread_v2_6_0`) so merging per-slot state is safe.
- Interactive charts: replaced the hand-rolled inline-SVG `LineChart`/
  `BarChart` (`charts.tsx`) and `RunOverview`'s equity curve with
  `uPlot`-backed equivalents (same props shape, so callers needed no changes
  beyond the equity curve itself) — labeled axes, a grid, a hover
  tooltip/crosshair, and drag-to-zoom-on-x (double-click to reset) that a
  hand-rolled SVG chart didn't have. The equity curve gains a filled-area
  treatment shaded to zero. New: a "Fills over time" step chart on the
  Performance tab, per slot — the backend now retains a capped running-total
  time series per slot (`fill_history`) alongside the existing latest-total
  snapshot the table already showed, since that snapshot alone had nothing
  to plot a trend from.
- Trade tape: dropped the hard 50-trade retention cap (`live.py`'s
  `TRADE_TAPE_LIMIT` previously discarded everything older, both for the
  in-memory live state and the on-demand completed-run read) — a run's full
  trade history is now held in memory for its lifetime.
  `GET /api/runs/{project}/{run_id}/trades?before=<ts>&limit=100` pages
  through it; `/overview` still only ever returns the latest 50 for first
  paint, so the initial load path is unchanged. The frontend trade tape is
  now a scrollable list that fetches older pages on demand as the operator
  scrolls, instead of rendering a fixed 50-row table.
- Two owed ADRs and this repo's own `CONTEXT.md`: `docs/adr/0001-...` covers
  the encrypted-log tiering/framing/detection design (per-line AEAD framing
  over whole-file or field-level, a self-describing header over
  config-driven detection); `docs/adr/0002-...` covers SIGTERM-over-SIGINT
  for stop, grounded in `tt-live-runner`'s `ShutdownCoordinator` escalation
  behavior. `CONTEXT.md` re-anchors "Operator console," "Run halt," "Control
  channel," and "Operator intervention" from rustle's own `CONTEXT.md`, now
  that tt-console is superseded. (#11)
- `RustleHealthLogAdapter` (`sources/rustle.py`) tails a live/shadow rustle
  run's `health_log.jsonl` — a ~5s-cadence snapshot of the same in-process
  Prometheus histograms `/metrics` would scrape (`md_latency`, `api_request`)
  — and wires it into the Latency tab, closing the gap noted above. Each
  snapshot's bucket counts are cumulative since process start, so a lone
  line says nothing about recent latency: the adapter keeps the previous
  snapshot per channel (grouped by feed for `md_latency`, by endpoint for
  `api_request`) and diffs consecutive ones, interpolating p99/p999 from the
  delta bucket counts (Prometheus's own `histogram_quantile` approach). A
  negative delta (the run's own process restarted, resetting the counters)
  is treated as a new baseline rather than reported as latency. Left alone:
  rustle's live health checks (component up/down), which nothing in
  `health_log.jsonl` maps onto yet.
- The rail can now be collapsed (a small tab on its right edge) to reclaim
  width for the run detail view, and expanded again via a floating button at
  the workspace's top-left corner.

### Changed

- Performance table's win rate now also shows the raw win-loss count (e.g.
  `83.3% (5-1)`), and a slot with an open (not-yet-flat) position is marked
  with a `*` next to its PnL: win/loss is only scored on a completed
  flat-to-flat round trip, so a slot can legitimately show a strong win rate
  next to negative PnL when its still-open position is currently a loser
  that hasn't closed (and been scored) yet — this was previously
  unexplained and looked like a bug.
- Market tab's price series is now read straight from each project's own
  independent market-data collector (`data/{symbol}/tick_data/{day}.txt`,
  written by `tt-collect`/`collect_mqtt_data_v4.py` outside this dashboard)
  instead of re-derived from the strategy's own trade-log fills. Fixes a
  multi-slot rustle run showing one fake per-slot "market" chart instead of
  one real chart per instrument, whenever no `config_path` slot→symbol
  mapping was available; also means the Market tab now works even when the
  trade log itself is encrypted-locked.
- Market tab no longer marks individual trade fills as dots on the price
  line — just the matched price series, which is what the tab is actually
  for; per-trade detail already lives in the trade log.
- Fills-over-time moved from the Performance tab to Overview, and now plots
  one aggregated total-fills series instead of a line per slot — the
  per-slot breakdown is still available as counts in the Performance table.
- Replaced `uPlot` with `lightweight-charts` for the equity curve, latency,
  market, per-slot fills, and run-comparison charts: real crosshair tooltips,
  native scroll/pinch zoom and drag-to-pan, and a proper legend, in place of
  the bare unstyled lines. `BarChart` (per-slot comparison) now draws grouped
  bars on a fake ordinal time axis, since the library has no native
  categorical axis — group labels are positioned by reading the chart's own
  time-to-pixel mapping rather than guessed via CSS, so they stay aligned
  under their cluster at any width.
- New Run flow: replaced each project's single configured binary
  (`SIGNAL_DECK_RUSTLE_BINARY` / `SIGNAL_DECK_TICKTRADER_BINARY`, always
  invoked as `<binary> --config <path>`) with a launch command per
  `(project, run_type)` plus a `cwd` per project —
  `SIGNAL_DECK_{RUSTLE,TICKTRADER}_{BACKTEST,LIVE}_CMD` and
  `SIGNAL_DECK_{RUSTLE,TICKTRADER}_CWD`. A project's live and backtest runs
  can be genuinely different binaries with different argv shapes (rustle:
  `cargo run -p tt-replay --bin tt-replay --release -- --config <path>` for
  backtest vs. `cargo run -p tt-live-runner --bin tt-live-runner --release --
  --config <path>` for live — both rustle binaries require an explicit
  `--config` flag, neither accepts a bare positional path), and cargo/
  `python -m` invocations need to run from inside the target repo, not this
  process's own working directory. The config path is always appended as
  the final argv token after `shlex.split`ing the configured command, never
  string-substituted, so a path with spaces can't reshape the command.
- New Run flow (rustle only): launches now also pass `--out
  <run_dir>/trade_log.jsonl`, forcing rustle's own trade log into this run's
  directory instead of its native `<mode>_<config-stem>/<date>/` path, which
  the dashboard has no way to resolve on its own — the date is picked
  internally by rustle from the config, not passed in. A config with a
  multi-date range can't be launched from the dashboard this way (`--out`
  only accepts a single resolved date); use a single-date config for
  dashboard-launched runs.
- `sources/rustle.py`'s `RustleAdapter` now parses rustle's real
  `trade_log.jsonl` schema (`slot_id, timestamp, type, best_bid, best_ask,
  spread, trade_price, trade_side, matched_volume, position, action, pnl` —
  `crates/tt-engine/src/trade_log_schema.rs`) instead of a schema that never
  matched any rustle output. `type` is always `"CONTROL"` in production and
  carries no signal; only `action == "FILLED"` rows are fills, and `pnl` is
  each slot's running realized-pnl snapshot, not a per-fill delta (both
  verified against a real run's output and tt-replay's own printed
  per-lane summary). Since the schema has no `symbol` column, a fill's
  instrument is resolved from the run's own config TOML
  (`[[multi_symbol.slots]] slot_label -> config.symbol`), read once at
  adapter construction from a new `config_path` field the process registry
  now writes into `run.json`; the bare `HH:MM:SS.mmm` timestamps are
  likewise anchored to the config's `from_date`. Win rate is derived
  per-slot (see Fixed, below, for the round-trip-vs-per-fill scoring
  detail).
- `/api/runs` (the run list) now polls every 60s instead of 5s - which runs
  exist barely changes minute to minute, and a run's own live PnL/equity
  already updates far faster via its Overview SSE stream once selected. A
  "⟳ Rescan" button in the rail triggers an immediate on-demand refresh.
- Removed the Overview tab's trade tape (raw fill-by-fill table) - unused.

### Fixed

- `GET /api/runs` no longer 500s when a run's log is encrypted — PnL
  computation now skips parsing (encrypted logs were previously handed
  straight to an adapter's `parse_line`, which expects plaintext). (#5)
- Win rate is now scored once per flat-to-flat round trip per slot instead
  of once per fill — a multi-fill trade (e.g. an opening fill followed by a
  closing fill) was previously counted as two separate win/loss events
  instead of one.
- `TickTraderLatencyAdapter` (`sources/ticktrader.py`) no longer resorts and
  re-averages its entire sample history on every incoming line — it now
  inserts into an already-sorted list (`bisect.insort`) and keeps a running
  sum instead. Discovered live: a real multi-strategy session's ~32k-sample
  `api_latency.jsonl` took the old approach past a minute to fully tail
  (blocking the live-poll loop's single-threaded event loop the whole time);
  the incremental version reads the same file in ~0.2s with identical output.
- `TickTraderTradeLogAdapter` never emitted `EquityPoint`s — every ticktrader
  run's Overview tab equity curve has always read "Not enough data yet.",
  since only rustle's adapter populated one. Now emits one on every
  pnl-bearing row: the sum of every slot's latest realized+unrealized, so a
  multi-slot run's curve is the account's total mark-to-market value, not
  just one slot's own delta.
- `TickTraderTradeLogAdapter`'s timestamps were bare seconds-since-midnight
  with no real date anchor (unlike rustle's adapter), so every chart's x-axis
  showed meaningless raw numbers (e.g. `41330`) instead of a time - and with
  time-axis formatting disabled entirely in `charts.tsx`, rustle's real epoch
  timestamps read just as poorly. ticktrader timestamps now anchor to a real
  date (`YYYYMMDD` parsed from the run dir's own name - TickTrader-para's
  convention embeds it directly - falling back to the manifest's
  `started_at`, then today), and uPlot's native time-axis formatting is on
  by default for `LineChart`/`StepChart` (`RunComparison`'s one
  normalized-progress-fraction chart opts out via `timeAxis={false}`).
- The equity curve was capped to its last 500 points, discarded oldest-first
  as new ones streamed in - for a fast-ticking run (ticktrader emits one
  point per pnl-bearing row, hundreds of thousands over a trading day) that
  meant the graph only ever showed the last ~70 seconds of activity, no
  matter when you opened the dashboard. Replaced the count cap with
  one-second time buckets (keep the latest point per bucket): bounded size
  (a trading day is tens of thousands of buckets, not hundreds of thousands
  of raw rows) that still spans the run's entire lifetime.
- The frontend re-introduced the same fixed-count equity cap on the client
  side (`RunOverview.tsx` sliced to the last 500 points on every SSE delta),
  undoing the backend's time-bucketed retention the moment a live run's
  first update arrived - only a run viewed *after* it ended (never receiving
  a delta) actually showed its full history. The frontend now buckets by the
  same one-second window as `live.py`. Also: `EventSource` auto-retries
  forever by default and can't tell a 404 (the run ended in the race between
  the initial `/overview` fetch and the `/stream` connection opening, two
  separate requests) from a transient blip - it now closes outright on any
  stream error instead of silently retrying.
- Switching the selected run left the Overview tab (equity curve,
  performance/market/latency panels) showing the previously-selected run's
  data: `<RunOverview>` was never keyed by run identity, so React patched the
  existing instance in place on a switch instead of remounting it - stale
  component state stuck around, and each uPlot chart kept its prior instance
  (and zoom range) rather than rebuilding against the new run's data. Now
  keyed by `project-run_id`, so a run switch always mounts a fresh instance.
- The run list's PnL column re-parsed each run's entire trade log from
  scratch on every poll (`discover_all_runs` via `/api/runs`), duplicating
  work the live-ingestion loop already does incrementally every second for
  live runs. A live run's PnL now comes straight from `LiveIngestionManager`'s
  already-tailed state instead of a from-scratch reparse.
- A manually-launched (manifest-less) run kept showing as "live" in the run
  list forever once its `runner.pid` PID number got reused by an unrelated
  process — `is_pid_alive` is just `os.kill(pid, 0)`, which can't tell a
  reused PID from the original one. Manifest-less runs older than 24h are no
  longer considered "worth tracking" regardless of PID liveness.
- The new rail-collapse button was easy to lose in the already-tight
  3-button `.rail-actions` row; moved it out to float on the rail's own
  border instead (mirroring the expand button's floating pattern), so it no
  longer competes with those buttons for space.
- Every `charts.tsx` time-axis chart (fills-over-time, market, latency, …)
  read wrong on two counts: lightweight-charts formats its own tick labels
  using UTC getters regardless of the browser's timezone, so the axis read
  hours off from the (correctly local-time) tooltip; and passing a raw
  fractional epoch (sub-second precision straight from Python) as `time`
  could round two adjacent points onto the same second, which the library
  rejects outright — throwing partway through a multi-series `setData` and
  silently killing every series after the one that collided, along with the
  `fitContent()` call that would otherwise have framed the whole run in one
  view. Timestamps are now shifted by the local UTC offset before being
  handed to the library (so its own UTC-based rendering lands on the correct
  local time) and rounded to whole seconds with same-second duplicates
  collapsed to the latest value.
- The run rail's PnL for the currently-open run sat on its own 60s
  `/api/runs` poll even though that run's Overview tab already had a live
  SSE stream open — so the rail could lag the Overview equity chart by up
  to a minute for a fast-moving live run. `RunOverview` now pushes its
  running total (`sum(realized + unrealized)` across slots, mirroring
  `live_manager.live_pnl_totals`) up on every SSE delta; the rail displays
  that in place of the polled value for that one run only, falling back to
  the poll once the run stops being live or its tab is closed. Run
  discovery itself is unaffected and still polls every 60s.
- `TickTraderTradeLogAdapter` counted every `type=="TRADE"` row as one of
  our own fills, but pinetree's stream emits that type for its own public
  market-trade prints too (trade side always `"UNKNOWN"`, PnL unchanged) —
  a busy market inflated the fills/trades count and win-rate denominator
  with prints that were never our executions. Only `type=="FILL"` or a
  populated `action` of `BUY`/`SELL`/`FILLED` is now treated as our own
  fill; every row (execution or not) still feeds the Market tab's price
  series.
- A multi-strategy ticktrader run's equity curve could silently regress:
  `_MergingAdapter.tail()` concatenated each source file's own running
  `equity` total in per-file processing order rather than real time order,
  so whichever file happened to be tailed last that tick would overwrite
  the merged total with its own file-local sum, discarding the other
  files' contributions. Equity is now recomputed from the merged,
  time-ordered pnl stream — a running per-slot realized+unrealized total
  summed across every source file on each new point.
