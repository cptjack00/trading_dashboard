"""Shared data model and adapter base class for log-reading sources.

Adapters tail a growing log file by byte offset and normalize new, complete
lines into the shared model below. Concrete adapters (e.g. `rustle.py`)
implement `parse_line`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, ClassVar

KeyResolver = Callable[[], bytes]
DecodeFn = Callable[[bytes, "bytes | KeyResolver | None", bool], bytes]


def _plaintext_decoder(raw_line: bytes, key: "bytes | KeyResolver | None", encrypted: bool) -> bytes:
    # ponytail: no AEAD decode implemented yet, even when the magic header marks a
    # file encrypted. Real per-line decryption arrives via a `decoder` passed to the
    # adapter once the writer-side scheme (key format, framing) is decided.
    return raw_line


@dataclass(frozen=True)
class Trade:
    ts: float
    symbol: str
    side: str
    price: float
    qty: float
    slot: str | None = None


@dataclass(frozen=True)
class EquityPoint:
    ts: float
    equity: float


@dataclass(frozen=True)
class RunStatus:
    run_id: str
    started_at: float
    updated_at: float
    state: str


@dataclass(frozen=True)
class HealthSample:
    ts: float
    component: str
    ok: bool
    detail: str | None = None


@dataclass(frozen=True)
class WinRate:
    ts: float
    slot: str
    wins: int
    losses: int


@dataclass(frozen=True)
class PnL:
    ts: float
    slot: str
    realized: float
    unrealized: float = 0.0


@dataclass(frozen=True)
class Fills:
    ts: float
    slot: str
    count: int


@dataclass(frozen=True)
class PricePoint:
    ts: float
    price: float
    trade: Trade | None = None


@dataclass(frozen=True)
class LatencySample:
    ts: float
    mean: float
    p99: float
    p999: float


@dataclass
class ParsedLog:
    """Accumulator for entries normalized from newly tailed lines."""

    trades: list[Trade] = field(default_factory=list)
    equity: list[EquityPoint] = field(default_factory=list)
    status: list[RunStatus] = field(default_factory=list)
    health: list[HealthSample] = field(default_factory=list)
    win_rates: list[WinRate] = field(default_factory=list)
    pnl: list[PnL] = field(default_factory=list)
    fills: list[Fills] = field(default_factory=list)
    symbol_prices: dict[str, list[PricePoint]] = field(default_factory=dict)
    channel_latency: dict[str, list[LatencySample]] = field(default_factory=dict)

    def add_price(self, symbol: str, point: PricePoint) -> None:
        self.symbol_prices.setdefault(symbol, []).append(point)

    def add_latency(self, channel: str, sample: LatencySample) -> None:
        self.channel_latency.setdefault(channel, []).append(sample)


class LogSourceAdapter(ABC):
    """Incrementally tails a log file and normalizes new lines into a `ParsedLog`.

    Construction accepts an opaque decryption `key` (or a resolver called to
    fetch one) plus a pluggable `decoder` seam. Decryption itself isn't
    implemented yet; the default decoder is a plaintext passthrough. The
    first bytes of the file are sniffed for `MAGIC_HEADER` — present, the
    header line is stripped and later lines are handed to the decoder with
    `encrypted=True`; absent, the file is (and stays) parsed as plaintext.
    """

    MAGIC_HEADER: ClassVar[bytes] = b"#SIGNAL-DECK-ENC-V1"

    def __init__(
        self,
        path: Path,
        *,
        key: bytes | KeyResolver | None = None,
        decoder: DecodeFn = _plaintext_decoder,
    ) -> None:
        self._path = path
        self._key = key
        self._decoder = decoder
        self._offset = 0
        self._buffer = b""
        self._header_checked = False
        self.encrypted = False

    def tail(self) -> ParsedLog:
        self._buffer += self._read_new_bytes()

        if not self._header_checked:
            if len(self._buffer) < len(self.MAGIC_HEADER) and self.MAGIC_HEADER.startswith(self._buffer):
                return ParsedLog()  # still a possible marker prefix; wait for more bytes
            self._header_checked = True
            if self._buffer.startswith(self.MAGIC_HEADER):
                self.encrypted = True
                self._buffer = self._buffer[len(self.MAGIC_HEADER) :]
                if self._buffer.startswith(b"\n"):
                    self._buffer = self._buffer[1:]

        *complete_lines, self._buffer = self._buffer.split(b"\n")

        result = ParsedLog()
        for raw_line in complete_lines:
            decoded = self._decoder(raw_line, self._key, self.encrypted)
            self.parse_line(decoded, result)
        return result

    def _read_new_bytes(self) -> bytes:
        with self._path.open("rb") as f:
            f.seek(self._offset)
            data = f.read()
            self._offset = f.tell()
        return data

    @abstractmethod
    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        """Parse one decoded, complete line and append entries to `into`."""


def is_encrypted(path: Path) -> bool:
    """Sniff a log file's header without tailing/parsing it.

    Callers that only need to know *whether* a file is encrypted (e.g. to
    decide whether it's safe to feed to an adapter at all, given no key
    resolution exists yet) use this instead of constructing an adapter and
    calling `tail()`, which would otherwise hand raw ciphertext bytes to
    `parse_line` and blow up.
    """
    header = LogSourceAdapter.MAGIC_HEADER
    try:
        with path.open("rb") as f:
            return f.read(len(header)) == header
    except OSError:
        return False
