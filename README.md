# Signal Deck

Unified trading dashboard for `rustle` and TickTrader-para. See `docs/spec/signal-deck-spec.md`
for the full design.

This first slice is the repo skeleton: FastAPI backend serving a built React
frontend as one process, Tailscale-only binding, and a shared-secret login.

## Dev

```sh
cd backend && uv sync
cd frontend && npm install
```

Run the backend (serves `/api/*`; frontend dev server proxies `/api` to it):

```sh
cd backend
SIGNAL_DECK_SECRET=dev-secret SIGNAL_DECK_HOST=127.0.0.1 uv run signal-deck
```

```sh
cd frontend
npm run dev
```

## Build

```sh
cd frontend && npm run build   # writes frontend/dist, served by the backend
cd backend && uv run pytest
```

`uv run signal-deck` serves `frontend/dist` directly once built — no separate
frontend process needed in production.

## Deploy

One process, one systemd unit — see `deploy/signal-deck.service` and
`deploy/signal-deck.env.example`. `SIGNAL_DECK_HOST` must be the Tailscale
interface address (`tailscale ip -4`); the app refuses to start bound to
`0.0.0.0`. No TLS is terminated in-app — Tailscale is the perimeter.
