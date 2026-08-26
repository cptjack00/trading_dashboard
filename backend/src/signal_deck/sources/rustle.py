"""Adapter for rustle's trade/latency logs: one JSON object per line.

Byte-offset tailing and marker/decoder handling live in `LogSourceAdapter`;
this module only maps a decoded, complete JSON line onto the shared model.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .base import (
    EquityPoint,
    Fills,
    HealthSample,
    LatencySample,
    LogSourceAdapter,
    ParsedLog,
    PnL,
    PricePoint,
    RunStatus,
    Trade,
    WinRate,
)


class RustleAdapter(LogSourceAdapter):
    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        text = line.strip()
        if not text:
            return
        event: dict[str, Any] = json.loads(text)
        event_type: str = event.get("type", "")
        handler = self._HANDLERS.get(event_type)
        if handler is None:
            return
        handler(self, event, into)

    def _handle_status(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.status.append(
            RunStatus(
                run_id=event["run_id"],
                started_at=event["started_at"],
                updated_at=event["updated_at"],
                state=event["state"],
            )
        )

    def _handle_trade(self, event: dict[str, Any], into: ParsedLog) -> None:
        trade = Trade(
            ts=event["ts"],
            symbol=event["symbol"],
            side=event["side"],
            price=event["price"],
            qty=event["qty"],
            slot=event.get("slot"),
        )
        into.trades.append(trade)
        into.add_price(trade.symbol, PricePoint(ts=trade.ts, price=trade.price, trade=trade))

    def _handle_price(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.add_price(event["symbol"], PricePoint(ts=event["ts"], price=event["price"]))

    def _handle_equity(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.equity.append(EquityPoint(ts=event["ts"], equity=event["equity"]))

    def _handle_health(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.health.append(
            HealthSample(
                ts=event["ts"],
                component=event["component"],
                ok=event["ok"],
                detail=event.get("detail"),
            )
        )

    def _handle_winrate(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.win_rates.append(
            WinRate(ts=event["ts"], slot=event["slot"], wins=event["wins"], losses=event["losses"])
        )

    def _handle_pnl(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.pnl.append(
            PnL(
                ts=event["ts"],
                slot=event["slot"],
                realized=event["realized"],
                unrealized=event.get("unrealized", 0.0),
            )
        )

    def _handle_fill(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.fills.append(Fills(ts=event["ts"], slot=event["slot"], count=event["count"]))

    def _handle_latency(self, event: dict[str, Any], into: ParsedLog) -> None:
        into.add_latency(
            event["channel"],
            LatencySample(ts=event["ts"], mean=event["mean"], p99=event["p99"], p999=event["p999"]),
        )

    _HANDLERS: dict[str, Callable[["RustleAdapter", dict[str, Any], ParsedLog], None]] = {
        "status": _handle_status,
        "trade": _handle_trade,
        "price": _handle_price,
        "equity": _handle_equity,
        "health": _handle_health,
        "winrate": _handle_winrate,
        "pnl": _handle_pnl,
        "fill": _handle_fill,
        "latency": _handle_latency,
    }
