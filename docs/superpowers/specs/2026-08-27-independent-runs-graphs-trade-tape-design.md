# Independent run tracking, interactive graphs, and a full trade tape

Design doc for three of the four items raised in the 2026-08-27 request (the
fourth — win rate miscounting per-fill instead of per round-trip trade — was
bounded and already shipped directly, see `sources/rustle.py` and
`CHANGELOG.md`).

## Problem statement

1. The dashboard only ever discovers runs it launched itself (a `run.json`
   manifest written by `process_control.py`). An operator who runs `rustle`
   or `ticktrader` by hand in a terminal gets no dashboard visibility at
   all, unlike TickTrader-para's old dashboard, which discovered whatever
   ran regardless of who started it.
2. `RunOverview`'s equity curve and `RunMarket`'s price chart are hand-rolled
   inline SVG (`charts.tsx`, `RunOverview.tsx`'s `EquityCurve`) with no
   labeled axes, no grid, and no interactivity (no tooltip, no zoom/pan).
3. The trade tape is hard-capped at the last 50 trades *ever* — both the
   in-memory live state (`live.py`'s `TRADE_TAPE_LIMIT`) and the on-demand
   completed-run read (`parsed.trades[-50:]`) permanently discard everything
   older than the most recent 50 fills. It's not paginated, just truncated,
   and the frontend renders whatever it gets as one flat unscrollable list.

## 1. Independent run tracking

### Discovery

`runs.py` gains a second discovery path alongside the existing
manifest-based `iter_manifests`: recursively scan each project's configured
runs root for directories containing a `trade_log.jsonl` (rustle) or
`trade_log.csv` (ticktrader) with **no** `run.json` next to them.

This has to be depth-agnostic because the two engines nest differently when
run directly (confirmed against the actual sibling repos, not guessed):

- rustle: `<runs_root>/<mode>_<config-stem>/<date>/trade_log.jsonl` — two
  levels deep (`tt-live-runner/src/main.rs`'s `live_output_paths`).
- ticktrader: `<runs_root>/<prefix>-<timestamp>/trade_log.csv` — one level
  deep (TickTrader-para's `reader.py`).

A fixed-depth scan would work for one and break the other, so the scan
walks arbitrarily deep under each runs root looking for a trade-log
filename, stopping descent into any directory once it's identified as a run
(no nested runs-within-runs).

### Run identity

`run_id` for a manifest-less run is its directory path relative to the
runs root, with `/` replaced by `__` (routes take `run_id` as a single path
segment) — e.g. `live_straddle_v1_test/20260826` becomes
`live_straddle_v1_test__20260826`. This is stable across dashboard restarts
without needing a generated UUID anywhere.

### Liveness

Both engines now write a `runner.pid` file (bare decimal PID) into the run
directory:

- ticktrader already did this (TickTrader-para's `reader.py` reads it with
  `os.kill(pid, 0)`, the same technique `process_control.is_pid_alive`
  already uses in this repo).
- rustle did not — **this is now fixed** in the sibling `rustle` repo:
  `tt-live-runner/src/main.rs` writes `runner.pid` next to the trade log for
  every live/shadow run (best-effort; a write failure is logged, never
  fails the run). See that repo's own `CHANGELOG.md` entry.

`is_pid_alive` moves out of `process_control.py` into a small shared
location (or is imported by `runs.py`) so manifest-less discovery uses the
exact same liveness check as dashboard-launched runs: `runner.pid` present
→ poll it; absent → status `unknown` (a fourth badge state alongside
live/stopped/crashed/backtest — distinct from both, not a guess).

### UI implications

- Stop button hidden for `unknown`-status runs — the dashboard never held
  this process's PID via its own registry and has no ownership story for
  it, so offering a stop control would be misleading.
- Run list badge for `unknown` reads distinctly (e.g. a neutral dot, not a
  red/green pulse) so it doesn't get confused with `crashed`.

### Explicitly out of scope

TickTrader-para's own `reader.py` has substantially more machinery than
this — per-account multi-session merging, missing-fill-report detection,
`{prefix}-*` child-session glob patterns for multiple concurrent accounts
under one prefix. None of that was asked for; replicating it would be
scope creep well beyond "pick up a run I started by hand." If a real need
for it shows up later, it's a separate, focused follow-up.

## 2. Interactive graphs

No charting library exists in the frontend today (`package.json` has only
`react`/`react-dom`). Getting real labeled axes, a grid, hover
tooltip/crosshair, pan/zoom, and filled areas by hand-rolling more SVG on
top of `charts.tsx`'s current ~70 lines would mean reinventing hit-testing
and zoom math that's already solved. Adding **`uplot`** (~45KB, zero
dependencies, canvas-based so it stays fast under live SSE updates) is the
better fit here.

- `charts.tsx`'s `LineChart`/`BarChart` and `RunOverview.tsx`'s inline
  `EquityCurve` are replaced by uPlot-backed components with the same
  props shape callers already use, so `RunOverview`/`RunMarket`/
  `RunPerformance` need minimal changes beyond the import.
- The equity/PnL curve (Overview tab) gets a filled-area treatment (shaded
  to zero) instead of a bare line.
- The market/price chart (buy/sell markers over price) keeps its markers,
  gains the same interactivity.
- New: a fills-over-time chart on the Performance tab — cumulative fill
  count per slot as a step/line series, next to the existing table. `Fills`
  data is already tracked per slot; today it only ever surfaces as a bare
  total in the table, never charted.

## 3. Trade tape

- Drop the 50-trade retention cap in `live.py` (`_LiveState.trades`'
  `[-TRADE_TAPE_LIMIT:]` slice, and `get_overview`'s
  `parsed.trades[-TRADE_TAPE_LIMIT:]` for completed runs). Adapters already
  tail incrementally byte-offset — this only changes what's *retained* in
  memory after tailing, not how tailing works. A run's total trade count is
  small enough (thousands, not millions) to hold in memory for its
  lifetime; no new storage layer.
- New endpoint: `GET /api/runs/{project}/{run_id}/trades?before=<ts>&limit=100`
  for on-demand older pages. `/overview` keeps returning just the latest 50
  for first paint — response shape unchanged there, no frontend breakage on
  the initial load path.
- `RunOverview.tsx`'s `TradeTape` becomes a scrollable list that fetches
  older pages via the new endpoint as the operator scrolls down, instead of
  rendering everything it's handed as one flat `<table>`.

## Testing

- Run discovery: fixture-based tests in `test_runs.py` — a manifest-less
  directory with each engine's real nesting depth (1-level ticktrader,
  2-level rustle) is discovered; a directory with neither a manifest nor a
  trade log is not; `runner.pid` present/absent drives `unknown` vs.
  live/stopped correctly, mirroring the existing PID-liveness tests in
  `test_process_control.py`.
- Trade pagination: `test_app.py`/`test_live.py` cases asserting the
  `/trades` endpoint pages correctly and that retention is no longer capped
  at 50.
- Frontend: no new test infra beyond what exists; manual verification via
  `run` (dev server) for the scrollable tape and the new charts, per this
  repo's existing "for UI changes, verify in a browser" practice.
- rustle's `runner.pid` change: covered by `cargo check`/existing
  `tt-live-runner` test suite (no test asserts the run directory's exact
  file listing outside an unrelated atomic-write temp-file check); no new
  Rust test added since the write is best-effort and side-effecting on a
  real live-trading path where minimal blast radius matters more than
  coverage of a monitoring nicety.

## Out of scope

- Rebuilding TickTrader-para's full session-discovery feature set (see
  above).
- Any change to how dashboard-launched runs are tracked — they keep using
  `process_control.py`'s existing registry/manifest path unchanged; the new
  discovery path only ever adds runs that path doesn't already know about.
- Persisting trade history to disk/a database — in-memory retention for a
  run's lifetime is sufficient at current trade-count scale.
