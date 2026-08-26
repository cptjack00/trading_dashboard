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
- New Run flow: a "New run" panel walks Project → Run type → Config (from
  #8's scan list), then a LIVE run requires an explicit arm toggle before
  Start is enabled while a BACKTEST run gets a plain, always-enabled "Start
  backtest" button with a "reads historical data only" note. Backend:
  `POST /api/runs` (`process_control.py`'s `ProcessRegistry.start_run`)
  `subprocess.Popen`s the project's configured binary
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
- Two owed ADRs and this repo's own `CONTEXT.md`: `docs/adr/0001-...` covers
  the encrypted-log tiering/framing/detection design (per-line AEAD framing
  over whole-file or field-level, a self-describing header over
  config-driven detection); `docs/adr/0002-...` covers SIGTERM-over-SIGINT
  for stop, grounded in `tt-live-runner`'s `ShutdownCoordinator` escalation
  behavior. `CONTEXT.md` re-anchors "Operator console," "Run halt," "Control
  channel," and "Operator intervention" from rustle's own `CONTEXT.md`, now
  that tt-console is superseded. (#11)

### Fixed

- `GET /api/runs` no longer 500s when a run's log is encrypted — PnL
  computation now skips parsing (encrypted logs were previously handed
  straight to an adapter's `parse_line`, which expects plaintext). (#5)
