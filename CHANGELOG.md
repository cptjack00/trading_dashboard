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
