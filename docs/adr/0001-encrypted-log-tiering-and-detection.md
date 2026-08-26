# ADR-0001: Encrypted-log tiering, framing, and detection

## Status

Accepted (design only — the writer-side encryption itself is not implemented in this repo; see spec §"Encrypted trade/fill logs" and §Out of Scope).

## Context

Signal Deck reads `rustle` and TickTrader-para's trade/fill logs directly off disk, by tailing them incrementally (byte-offset, newline-delimited). `rustle` is moving toward a colocated live-trading deployment, where the box running the trading engine is no longer fully trusted infrastructure the way the current one is — a trade/fill log leaving that box (backup, transfer, compromise) becomes a real disclosure risk. The dashboard needs a reading-side seam for encrypted logs *now*, even though the writer-side encryption (inside the Rust trading engines) is built later, so that adding it doesn't force a redesign of the tailer.

Three tiers of encryption granularity were considered:

1. **Whole-file encryption** — encrypt the entire log file (or wrap it in an encrypted container) and decrypt it wholesale before reading.
2. **Per-line AEAD framing** — encrypt each JSONL record independently, preserving the newline-delimited framing exactly.
3. **Field-level encryption** — encrypt only specific sensitive fields within an otherwise-plaintext JSON record.

And two detection mechanisms:

- **Self-describing header** — a magic marker at the start of the file declares its format.
- **Config-driven** — the dashboard is told out-of-band (a flag, a per-run setting) whether a given log is encrypted.

## Decision

**Tier 2 (per-line AEAD framing)**, detected via a **self-describing magic header**.

### Why tier 2 over tier 1 (whole-file)

The dashboard's core read model is incremental tailing: read new bytes since the last offset, split on newlines, parse whatever complete lines exist, and stop at a partial line still being written. Whole-file encryption is fundamentally incompatible with that model — a growing live log encrypted as one blob can't be decrypted until the writer closes the file (or the tailer buffers and re-decrypts the whole thing on every poll, which defeats the point of incremental tailing and would visibly degrade poll-loop latency as a live log grows over a multi-hour session).

Per-line framing keeps the incremental read path completely unchanged: "find complete lines since the last offset" is exactly the same logic whether those lines are plaintext or ciphertext. Only the "parse one complete line" step gains a decrypt-then-parse branch. This was the deciding factor — it's a one-line seam (see `sources/base.py`'s `DecodeFn` / `_plaintext_decoder`) rather than a rewrite of `LogSourceAdapter.tail()`.

### Why tier 2 over tier 3 (field-level)

Field-level encryption would require the decoder to understand each project's record schema (rustle's event types vs. TickTrader-para's CSV columns) to know which fields to decrypt, coupling the encryption seam to both adapters' parsing logic instead of sitting cleanly below it. It also leaves the record's *shape* (event type, timestamps, non-sensitive fields) in plaintext, which does narrow the actual disclosure — trade price/side/quantity is exactly what's sensitive — but the mechanical cost (per-field key derivation, per-project schema awareness in the encryption layer) wasn't judged worth that narrower disclosure surface for a first cut. Per-line framing treats the sensitive payload as opaque and keeps the seam schema-agnostic; both adapters share it as-is.

### Why self-describing header over config-driven

A self-describing header (checked once per file, before any line parsing starts) means the dashboard never needs coordination between how a run was launched and how its logs get read: an old plaintext log from before encryption shipped, and a new encrypted log from after, are both just files the tailer sniffs and handles correctly, forever, with no flag-day cutover and no per-run "is this encrypted?" setting to keep in sync with reality. Config-driven detection creates exactly the failure mode this is meant to avoid: a mismatch between the config's belief and the file's actual format (stale config, launched-by-hand run, a project migrating mid-rollout) silently either feeds ciphertext to a plaintext parser or vice versa. A magic header is checked against the bytes that are actually there.

## Consequences

- `LogSourceAdapter` sniffs `MAGIC_HEADER` on the first bytes of a file, strips it once found, and hands `encrypted=True` to the pluggable `decoder` for every subsequent line; absent, the file is (and stays) parsed as plaintext for its lifetime.
- The decoder itself is not implemented yet (`_plaintext_decoder` is a passthrough regardless of the `encrypted` flag) — key resolution (env var, operator-entered secret, KMS) is explicitly deferred to whenever the writer-side scheme is built.
- Until a key is configured, `is_encrypted()` lets the dashboard detect an encrypted file without attempting to parse it, so a run with no key configured degrades to "🔒 encrypted — no key configured" for Overview/Performance rather than crashing or hiding the run.
- The seam lives on the shared `LogSourceAdapter` base class, used by both `sources/rustle.py` and `sources/ticktrader.py`, even though only `rustle` currently plans to colocate — cheap now, avoids a second redesign if TickTrader-para's live trading ever moves too.
