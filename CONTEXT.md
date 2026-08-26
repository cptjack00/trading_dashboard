# Context: Signal Deck

The unified trading dashboard for `rustle` and TickTrader-para. Replaces `rustle`'s `tt-console` (which this document calls **rustle's operator console** below) and supersedes TickTrader-para's own dashboard as the UI of record for both projects. See `docs/spec/signal-deck-spec.md` for the full design; this file anchors the vocabulary.

## Terms this repo re-anchors from rustle's operator console

`rustle`'s own `CONTEXT.md` defines several terms for the console this repo replaces. Those terms described a control-socket-based architecture with a real command-audit surface; Signal Deck's control surface is deliberately much narrower (see spec §"Process control", §Out of Scope). Rather than reuse the old names for a materially different design, this repo either drops the term or gives it a narrower, explicit replacement:

- **"Operator console"** → **Signal Deck**, or just "the dashboard." Signal Deck reads log files directly (incremental, byte-offset tailing) instead of consuming a control-socket stream, and covers both `rustle` and TickTrader-para from one process — it is not a like-for-like replacement of the old console's architecture, only of its role.
- **"Control channel"** — **retired, no replacement**. The old Control channel's full ceremony (`CommandBinding`, proposer/approver, idempotent command IDs, a durable audited journal) is explicitly not rebuilt here. Signal Deck's entire control surface is two actions: **start a run** and **stop a live run**. Nothing in this repo is "the control channel"; don't reuse the term for the start/stop HTTP endpoints — they're not a channel, they're two plain POST routes with no proposer/approver ceremony.
- **"Run halt"** → **stop**. The old vocabulary's "halt" implied a broader command surface (halt/cancel/flatten) that this dashboard does not expose. What this repo calls **stop** is narrower and specific: it sends SIGTERM to the tracked process (see ADR-0002) and nothing else — no separate cancel or flatten command exists at the dashboard layer (the engine's own SIGTERM-triggered cancel-and-flatten sequence is internal to `tt-live-runner`, not something the dashboard invokes as a distinct step). Don't use "halt" in this repo's code, issues, or docs for the stop action — it overclaims what the button does.
- **"Operator intervention"** → **start** and **stop**, named individually. The old term covered a wider space of possible commands; here there are exactly two operator-initiated actions, and each has its own name (see below). Don't use "operator intervention" as an umbrella term in this repo — name the specific action.

## This repo's own vocabulary

- **Run** — one execution of a trading strategy, either `rustle` or TickTrader-para, in one of four states: `live`, `stopped`, `crashed`, `backtest`. A run is identified by `(project, run_id)` and has a directory (under that project's runs root) holding a `run.json` manifest plus that project's own log file(s).
- **Manifest** (`run.json`) — the small per-run JSON file (`run_id`, `run_type`, `state`, `started_at`, `ended_at`, ...) that `runs.py` reads to classify and list a run. For a run launched from this dashboard, the manifest is written by the **process registry** at start time and updated by **reconcile** as the process's liveness changes; see `process_control.py`.
- **Process registry** — the persisted record (`process_registry.json` by default) of PID, start time, run directory, and stop-request state for every run this dashboard instance has launched. Survives a dashboard restart. Not the same thing as the manifest: the registry is this dashboard's private bookkeeping for control (who do I signal to stop this?); the manifest is the public run-classification record the run list reads.
- **Start** — the New Run flow (#9): pick a project, a run type (`live` or `backtest`), and a config file, then `subprocess.Popen` the project's configured binary with `--config <path>`. A LIVE start requires an explicit arm toggle before the Start button is enabled; a BACKTEST start does not.
- **Stop** — sends SIGTERM to a live run's tracked PID (ADR-0002), gated by an inline confirm step in the UI, deduplicated so a repeated stop request against the same run sends at most one SIGTERM, and always durably logged (`stop_events.log`) regardless of whether that request was the first.
- **Reconcile** — the periodic PID-liveness pass (`ProcessRegistry.reconcile`, run on the same cadence the frontend already polls `/api/runs` at) that updates a tracked run's manifest from `live` to `stopped` or `crashed` once its process has exited.
- **Encrypted-locked** — a run whose trade/fill log is detected (via the self-describing magic header — ADR-0001) as encrypted, with no decryption key currently configured. Health and Latency stay fully visible for such a run; only trade-derived charts (Overview, Performance) show a "🔒 encrypted — no key configured" placeholder.

## Where to look next

- `docs/spec/signal-deck-spec.md` — the full design spec this repo implements.
- `docs/adr/0001-encrypted-log-tiering-and-detection.md` — why per-line AEAD framing + a self-describing header, over the alternatives.
- `docs/adr/0002-sigterm-over-sigint-for-stop.md` — why stop sends SIGTERM, grounded in `tt-live-runner`'s actual `ShutdownCoordinator` behavior.
