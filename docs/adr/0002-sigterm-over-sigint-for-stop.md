# ADR-0002: SIGTERM, not SIGINT, for the stop action

## Status

Accepted.

## Context

Stopping a live run from the dashboard has to signal the process the dashboard itself launched (tracked by PID in the process registry — see #9). The choice of signal isn't cosmetic: `rustle`'s live trading engine (`tt-live-runner`) has a `ShutdownCoordinator` (`crates/tt-live-runner/src/shutdown.rs`) with signal-specific escalation behavior that a stop action from a UI button has to respect, not fight.

`ShutdownCoordinator` counts **SIGINT** specifically:

- The first SIGINT transitions `RUNNING → GRACEFUL` — starts the cancel-and-flatten sequence (cancel open orders, flatten positions, then exit).
- A second SIGINT is treated as "abandon graceful shutdown, hard exit immediately" (`std::process::exit(2)`).

This is a deliberate CLI convention: a human at a terminal who presses Ctrl+C twice is understood to mean "I know graceful shutdown is in progress, I don't want to wait, force it." It's the right behavior for a terminal session. It is the **wrong** behavior for a networked button: a dashboard's Stop control can be double-clicked, retried after a flaky connection, or fired twice by an operator unsure whether the first click registered — and none of those are "abandon the graceful cancel-and-flatten sequence, exit immediately mid-flatten," which is what a second SIGINT would trigger against a live trading engine. Reusing SIGINT would make the dashboard's stop action *unsafe to repeat* — the exact opposite of the idempotency issue #10 requires (a repeated stop must produce no more than one effective termination attempt, not "the second click hard-kills mid-flatten").

**SIGTERM**, by contrast, drives the identical `RUNNING → GRACEFUL` transition with **no escalation path** — a repeated SIGTERM against an already-`GRACEFUL` engine is `AlreadyGraceful`, a no-op.

## Decision

The dashboard's stop action sends **SIGTERM**, never SIGINT.

This makes the stop button idempotent *at the engine level*, for free: however many times SIGTERM arrives, the engine runs the cancel-and-flatten sequence exactly once and ignores the rest. The dashboard doesn't need to reproduce any part of the old Control channel's command-deduplication ceremony (`CommandBinding`, proposer/approver, idempotent command IDs) to get that guarantee — it falls out of the signal choice itself. (The dashboard's `ProcessRegistry.stop_run` still adds its own request-level dedup — a `stop_requested_at` marker gating the actual `os.kill` call — so the guarantee holds deterministically in tests against a bare fake script that has no `ShutdownCoordinator`-equivalent of its own, not only against the real engine.)

## Consequences

- A rapid double-stop from the dashboard is safe by construction: worst case, two SIGTERMs are sent (or, with the registry's own dedup, exactly one), and the engine's own idempotent handling absorbs any redundancy either way.
- The dashboard never needs a "force stop" / hard-kill affordance to recover from a bad double-click — there's no escalation path to accidentally trigger. If an operator genuinely needs to hard-kill a wedged process, that's outside this control surface entirely (matches the spec's explicit exclusion of halt/cancel/flatten commands from the dashboard).
- This is specific to `tt-live-runner`'s actual shutdown implementation, not a general "always prefer SIGTERM" policy — the decision is grounded in `ShutdownCoordinator`'s documented SIGINT-escalation behavior, and would need revisiting if that behavior changes.
- A durable "operator stopped run `<name>` at `<time>`" record is still written on every stop request (see `ProcessRegistry._append_stop_record`) — for accountability, not safety, since safety is now guaranteed by the signal choice itself.
