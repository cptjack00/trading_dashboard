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

### Fixed

- `GET /api/runs` no longer 500s when a run's log is encrypted — PnL
  computation now skips parsing (encrypted logs were previously handed
  straight to an adapter's `parse_line`, which expects plaintext). (#5)
