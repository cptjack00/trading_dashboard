# Signal Deck — Unified Trading Dashboard

*Working title, taken from the UI mockup. Not confirmed as the final product name. This spec covers a brand-new standalone repository — it does not modify `rustle` or `TickTrader-para` directly, though it retires `tt-console` from `rustle` as a consequence (see Out of Scope).*

## Problem Statement

The operator currently runs two separate, unrelated tools to watch and control trading strategies:

- **tt-console** (in `rustle`): a Rust/axum backend with a React-ish frontend that the operator considers an ugly prototype. It streams telemetry to the UI over a Unix control socket, which the operator distrusts as a potential source of latency risk to live trading, even though in practice the control socket only carries low-frequency status/command traffic (see Implementation Decisions). It also owns real trading-process supervision: launching `tt-live-runner`, and issuing operator commands over an audited control channel.
- **TickTrader-para's dashboard** (`ticktrader/dashboard/`): a more mature, working Python tool that tails trade/latency log files directly (no socket) and renders server-side HTML. It has already solved real edge cases — incremental byte-offset CSV/JSONL tailing, missing-fill-report detection, multi-session discovery — but its UI is dated (raw HTML strings) and it only knows about TickTrader-para's own runs.

Neither tool serves both projects, and the operator wants one dashboard, not two separate controllers, especially as `rustle` moves toward colocated live trading where operational simplicity and trust in the data path both matter more.

## Solution

Build one new, standalone dashboard — a fresh repository, not a subdirectory of either existing project — with a Python (FastAPI) backend and a React (Vite) frontend, replacing tt-console entirely and superseding TickTrader-para's dashboard as the UI of record for both projects.

The backend reads log files directly (incremental, byte-offset tailing) instead of consuming a control-socket stream, for both projects, from one running service. TickTrader-para's proven tailing *approach* is re-implemented (not imported as a dependency) so the new repo carries no runtime coupling to either existing codebase. Operator control from the dashboard is deliberately narrow: start a run (live or backtest, for either project) and stop a live run — nothing else. The design leaves an explicit, unimplemented seam for encrypted trade/fill logs, anticipating a future colocated deployment, without building the actual encryption now.

A full interaction-design reference mockup was built during design and is available for implementation reference (see Further Notes).

## User Stories

**Observability**

1. As an operator, I want to see all runs (live, stopped, backtest, crashed) across both `rustle` and TickTrader-para in one list, so I don't need two separate tools.
2. As an operator, I want each run's live/dead status to be visually obvious at a glance (a heartbeat/pulse indicator), so I can tell in seconds whether a strategy is still alive without opening it.
3. As an operator, I want a live-updating equity curve for a running strategy, so I can watch its PnL trend in real time without relying on a low-latency streaming socket.
4. As an operator, I want to see the recent trade tape (time, side, quantity, price) for a run, so I can verify what it's actually doing.
5. As an operator, I want per-slot win rate, PnL, and fill count broken out, plus an overall total, so I can see which trading intervals are underperforming rather than only the run-wide number.
6. As an operator, I want to see matched price movement for a run's tracked symbols, with buy/sell markers plotted at the actual trade points, so I can see fills in their market context.
7. As an operator, I want ws/api latency shown as mean, p99, and p999 with trend graphs, so I can catch degrading connectivity before it becomes a trading problem.
8. As an operator, I want latency/health detail hidden for backtest runs, so the UI never shows meaningless "network latency" for a run that touched no network.
9. As an operator, I want an unambiguous BACKTEST badge and a real duration/date-range shown for backtest runs, so I never confuse a backtest with a live run that simply stopped.
10. As an operator, I want to select two or more runs, in any mix of project and status, and see PnL, fills, and win rate compared side by side, so I can evaluate backtest-vs-backtest, backtest-vs-live, or live-vs-live without leaving the dashboard.
11. As an operator, I want per-slot PnL/fill comparison shown as grouped bars only when the selected runs share the same slot count, and a plain explanation otherwise, so I'm never shown a misleading alignment between runs that don't actually correspond slot-for-slot.
12. As an operator, I want an overlaid cumulative-PnL chart across the runs I'm comparing, normalized by run-progress fraction rather than wall-clock time, so I can compare shape even when one run is 45 minutes and another is a 6-hour backtest.
13. As an operator, I want the run list to show enough at a glance (project, status, elapsed/duration, PnL) that I rarely need to open a run just to check whether it needs attention.

**Control**

14. As an operator, I want to start a new run by picking a project, a run type (live or backtest), and a config file from a dropdown, so I don't need a terminal to launch a strategy.
15. As an operator, I want the config dropdown populated by a recursive, multi-level directory scan, so nested config layouts are fully discoverable.
16. As an operator, I want to add a new directory to the config scan from the UI, so I'm not limited to whatever roots were configured at deploy time.
17. As an operator starting a LIVE run, I want an explicit two-stage arm-and-confirm flow — pick, then a distinct "arm" toggle that must be checked before start becomes clickable — so I can't fat-finger a real trading process into existence.
18. As an operator starting a BACKTEST run, I want a lighter single-confirm flow with no arm toggle or danger framing, so launching something that places no live orders doesn't carry the same ceremony as launching real trading.
19. As an operator, I want to stop a live run through one clearly-labeled action with an inline confirm step, so I can intervene quickly but not by accident.
20. As an operator, I want a stop request to be safe to send more than once, so a flaky connection, or my own uncertainty about whether the first click registered, can never abort an in-progress cancel-and-flatten sequence.
21. As an operator, I want every stop request durably recorded (who, which run, when), so there's accountability for who stopped a live run even though the dashboard doesn't rebuild the old full command-audit ceremony.

**Colocation / encrypted logs (design-only in this spec)**

22. As an operator preparing to colocate, I want the trade/fill log format to support per-line encrypted records without changing how the dashboard tails files, so building the real encryption later doesn't require redesigning the reading side.
23. As an operator, I want the dashboard to detect an encrypted trade log automatically, via a self-describing marker in the file, rather than relying on separate configuration, so there's no chance of a mismatch between how a run was launched and how its logs get interpreted.
24. As an operator, I want the dashboard to show "encrypted — no key configured" in place of trade-derived charts when it can't decrypt a run's trade log, rather than erroring out or hiding the run entirely, so I can still see that a live run exists and is healthy before decryption is wired up.
25. As an operator, I want health and latency telemetry to stay fully visible even when a run's trade log is encrypted and undecryptable, so I don't lose live-monitoring visibility over data that was never sensitive to begin with.

**Shared infrastructure**

26. As an operator, I want the dashboard reachable over Tailscale with a lightweight login, not the full TLS/Argon2id/systemd-credential ceremony tt-console used, so remote access stays easy without rebuilding LAN-hardening that no longer matches how I actually reach the box.
27. As a maintainer, I want TickTrader-para's log-tailing logic re-implemented rather than imported, so the new dashboard carries no runtime dependency on the TickTrader-para codebase.
28. As a maintainer, I want both projects' log adapters to share one base interface, including the future encrypted-log decoder seam, so a capability added for one project (e.g. colocation support) doesn't require a second redesign if the other project ever needs it too.

## Implementation Decisions

**Architecture & deployment**

- New standalone repository. Python backend (FastAPI + uvicorn), React (Vite) frontend built to static assets and served by the same process — one deployable, one systemd unit, replacing tt-console's unit/Caddy proxy.
- Binds to the Tailscale interface (not `0.0.0.0`); no TLS termination inside the app.
- Auth: a single shared secret issuing a signed, httpOnly session cookie using stdlib-level primitives (e.g. `hmac`/`secrets`) — no external auth framework, no Argon2id/systemd-credential ceremony. This is a deliberate reduction from tt-console's posture, justified by Tailscale being the actual network perimeter now.

**Log adapters & data model**

- One adapter per project (`sources/rustle.py`, `sources/ticktrader.py`) sharing a common base class/interface, normalizing into a shared internal model: `Trade`, `EquityPoint`, `RunStatus`, `HealthSample`, plus per-slot `WinRate`/`PnL`/`Fills`, per-symbol matched-price series with trade markers, and per-channel (ws/api) latency percentile series (mean/p99/p999).
- TickTrader-para's `reader.py` incremental byte-offset tailing *approach* is re-implemented from scratch, not imported.
- Ingestion: an asyncio poll loop (~1s) per *active* (live) run reads new bytes since the last offset, parses complete new lines, updates in-memory state, and pushes deltas to subscribed clients over SSE. Completed runs (stopped/backtest/crashed) are read on demand and cached — no background polling for them.
- Run classification: `live`, `stopped`, `backtest`, `crashed`, each with a visually and textually distinct treatment. (Shadow mode — a third run type that exists in `rustle`'s domain model — was explicitly ruled out of scope for this dashboard; see Out of Scope.)

**Comparison**

- Any 2–4 selected runs, regardless of project or status, can be compared on PnL, fills, and win rate (a table, with a delta column shown only when exactly 2 runs are selected).
- An overlaid cumulative-PnL chart, one line per run, normalized by run-progress fraction (index/length) rather than wall-clock time or point count, since compared runs can have wildly different durations and log densities.
- Grouped per-slot PnL/fill bar charts, gated on all selected runs having equal slot counts; otherwise a plain fallback message replaces the chart rather than rendering a misleading alignment.

**Process control**

- Start: `subprocess.Popen` spawns the chosen binary with `--config <path>`; stdout/stderr redirect to a per-run log file; PID and start time are tracked in a registry persisted to a small local state file (survives a dashboard restart). Liveness is detected by periodic PID polling.
- Stop uses **SIGTERM, not SIGINT**. Per `rustle`'s `crates/tt-live-runner/src/shutdown.rs`, `ShutdownCoordinator` counts SIGINTs specifically: the first transitions `RUNNING → GRACEFUL` (starts the cancel/flatten sequence); a second is treated as "abandon graceful shutdown, hard exit immediately" (`std::process::exit(2)`) — a deliberate CLI double-Ctrl-C convention for a human at a terminal, wrong for a networked button that might get double-clicked. SIGTERM drives the identical `RUNNING → GRACEFUL` transition with no escalation path; a repeated SIGTERM is a no-op (`AlreadyGraceful`). This makes the dashboard's stop action naturally idempotent at the engine level, with no application-side command deduplication required.
- A durable "operator stopped run `<name>` at `<time>`" record is still kept — for accountability, not safety, since safety is now guaranteed by the signal choice itself.
- No halt/cancel/flatten commands are exposed from the dashboard, and the old Control channel's full ceremony (`CommandBinding`, proposer/approver, idempotent command IDs, durable audited journal) is not rebuilt. Start and stop are the entire control surface.

**Config discovery**

- A local settings store holds a configurable list of root directories to scan, per project.
- The backend does a recursive (`**/*.toml`), multi-level scan of those roots on demand (e.g. when the New Run modal opens).
- The UI can add a new root directory to the scan list. No upload, catalog, or validation wizard.

**New Run flow**

- Stage 1: pick Project, then Run type (LIVE or BACKTEST), then Config (from the scanned list).
- Stage 2, LIVE: an explicit "arm" toggle ("this launches a real trading process") that must be checked before Start is enabled.
- Stage 2, BACKTEST: a plain, immediately-enabled "Start backtest" button, with a note that it reads historical data only — no arm toggle, no danger framing.

**Encrypted trade/fill logs — colocation readiness (design only; not implemented in this spec)**

- **Scope**: the trade/fill log only (`account.trade_log.jsonl` / `fills.jsonl`-equivalent), for **live** runs only. Backtests never run at the colo. Health/latency telemetry and raw stdout/stderr are out of scope — not sensitive, and a poor mechanical fit for line-oriented AEAD framing.
- **Framing**: encryption (built later, on the writer side — the Rust trading engines, *not* this repo) is per-line AEAD: one encrypted record per line, preserving the existing newline-delimited JSONL framing exactly. The incremental tailer's "find complete lines since the last offset" logic needs zero changes; only the "parse one raw line into a record" step gains a decrypt-then-parse branch.
- **Detection**: self-describing. A magic header/marker at the start of a file declares its format; the decoder checks for it and switches modes per file automatically. No coordination is needed between how a run was launched and how the dashboard reads its logs — old plaintext logs stay readable forever, with no flag-day cutover.
- **Seam placement**: the pluggable line-decoder abstraction lives on the *shared* adapter base class, used by both `sources/rustle.py` and `sources/ticktrader.py`, even though only `rustle` currently plans to colocate. This is cheap now and avoids a second redesign if TickTrader-para's live trading ever moves too.
- **Key resolution is explicitly deferred**: the decoder seam accepts an opaque key or key-resolver at construction time. Where that key actually comes from (env var, operator-entered secret, KMS) is left to whenever the writer-side encryption ships — not decided here.
- **Degraded UX**: when an encrypted trade log is detected with no key configured, the run still shows its real status (e.g. LIVE) with Health and Latency tabs fully functional; only the Overview equity/trade-tape and the Performance tab show a "🔒 encrypted — no key configured" placeholder in place of the normal charts.
- Two decisions from this section are flagged ADR-worthy (see Further Notes): the encryption tiering/framing/detection design, and SIGTERM-over-SIGINT for stop.

## Testing Decisions

- Good tests here exercise external behavior — what a run's parsed state looks like after feeding an adapter a log fixture, not the internals of the byte-offset bookkeeping.
- Each log adapter (`sources/rustle.py`, `sources/ticktrader.py`) gets an offline test against a small, redacted fixture log file, verifying incremental resume (feeding the file in two chunks produces the same result as one) and partial-line handling (a line cut mid-write is not parsed until it's complete). No real or generated production logs in fixtures — following `rustle`'s existing `AGENTS.md` convention, worth carrying into the new repo.
- Process control (start/stop) is tested against a fake sleep/echo script standing in for the real trading binaries — not against actual `tt-live-runner` or TickTrader-para processes.
- Once process control is implemented, add an explicit test for the stop-idempotency property: two rapid stop calls against the fake script must not produce two distinct termination attempts, mirroring the no-op guarantee `tt-live-runner` already provides for a repeated SIGTERM.
- There is no existing test seam to reuse — this is a new repository with no prior test infrastructure. Adapters and process control are the two natural first seams, since they're the modules most exposed to real-world log-format and process-lifecycle edge cases.

## Out of Scope

- Publishing this spec to an issue tracker or applying triage labels — explicitly declined for this pass; Markdown only, to be moved into the new repo by hand.
- Shadow run mode — a third run type in `rustle`'s domain model (alongside backtest and live) — explicitly dropped from this dashboard's scope mid-design.
- Halt/cancel/flatten operator commands, and tt-console's full Control channel ceremony (`CommandBinding`, proposer/approver, idempotent command IDs, durable audited journal).
- A config upload/catalog/validation wizard — only a dropdown over a scanned directory list is in scope.
- Actual implementation of trade-log encryption (the AEAD writer side, inside the Rust trading engines) — this spec covers only the dashboard-side reading seam.
- The key-management mechanism for the future decryption key (env var vs. operator-entered secret vs. KMS) — deferred to whenever the writer-side encryption is actually built.
- LUKS/volume-level disk encryption — explicitly rejected in favor of file-level encryption that survives the file leaving the box.
- TLS termination and Argon2id-style auth inside the app — replaced by the Tailscale perimeter plus a lightweight session cookie.
- Deleting `crates/tt-console/`, `deploy/tt-console/`, `tools/run-console-local.sh`, and `configs/tt-console.example.toml` from `rustle` — a real consequence of this work, but a separate change to `rustle` itself, not part of building the new dashboard.

## Further Notes

- Working title "Signal Deck," taken from the UI mockup built during design; not confirmed as the final product name.
- A full interaction-design reference exists as a static HTML mockup (fabricated fixture data, not production code) covering the run list, tabbed run detail (Overview / Performance / Market / Latency), the comparison view, and the New Run flow: https://claude.ai/code/artifact/df9c24b7-4d9d-4821-9725-d378ef15c6f4 — treat it as the visual/interaction reference during implementation.
- Two ADRs are owed once the new repository exists (both meet the hard-to-reverse / non-obvious / genuine-trade-off bar):
  1. The encrypted-log tiering, framing, and detection design (tier 2 chosen over tiers 1 and 3; self-describing header chosen over config-driven detection).
  2. SIGTERM over SIGINT for the stop action, and why — grounded in `tt-live-runner`'s actual shutdown-escalation code, not a general policy.
- The new repository will need its own `CONTEXT.md`. Several terms this spec leans on — "Operator console," "Run halt," "Control channel," "Operator intervention" — are currently defined in `rustle`'s own `CONTEXT.md` for the system this dashboard replaces, and will need to be re-anchored or explicitly superseded once tt-console is retired.
