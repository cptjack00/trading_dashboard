from __future__ import annotations

from pathlib import Path

from signal_deck.sources.base import LogSourceAdapter, ParsedLog, Trade


class _EchoAdapter(LogSourceAdapter):
    """Minimal adapter for exercising the base class: one trade per line."""

    def parse_line(self, line: bytes, into: ParsedLog) -> None:
        into.trades.append(Trade(ts=0.0, symbol=line.decode().strip(), side="buy", price=1.0, qty=1.0))


def test_tail_reads_only_new_bytes(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_text("AAA\n")
    adapter = _EchoAdapter(path)

    first = adapter.tail()
    assert [t.symbol for t in first.trades] == ["AAA"]

    with path.open("a") as f:
        f.write("BBB\n")
    second = adapter.tail()
    assert [t.symbol for t in second.trades] == ["BBB"]


def test_incomplete_trailing_line_not_parsed_until_complete(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_text("AAA\n")
    adapter = _EchoAdapter(path)
    adapter.tail()

    with path.open("a") as f:
        f.write("BB")  # no trailing newline yet - write is mid-flight
    mid_write = adapter.tail()
    assert mid_write.trades == []

    with path.open("a") as f:
        f.write("B\n")  # write completes
    completed = adapter.tail()
    assert [t.symbol for t in completed.trades] == ["BBB"]


def test_chunked_feed_matches_single_feed(tmp_path: Path):
    lines = [f"L{i}\n" for i in range(5)]
    content = "".join(lines)

    whole_path = tmp_path / "whole.txt"
    whole_path.write_text(content)
    whole_adapter = _EchoAdapter(whole_path)
    whole_result = whole_adapter.tail()

    chunked_path = tmp_path / "chunked.txt"
    chunked_path.write_text("")
    chunked_adapter = _EchoAdapter(chunked_path)
    chunked_symbols: list[str] = []
    midpoint = len(content) // 2
    for chunk in (content[:midpoint], content[midpoint:]):
        with chunked_path.open("a") as f:
            f.write(chunk)
        chunked_symbols.extend(t.symbol for t in chunked_adapter.tail().trades)

    assert chunked_symbols == [t.symbol for t in whole_result.trades]


def test_plaintext_log_without_marker_parses_normally(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_text("AAA\nBBB\n")
    adapter = _EchoAdapter(path)
    result = adapter.tail()
    assert [t.symbol for t in result.trades] == ["AAA", "BBB"]


def test_magic_header_is_detected_and_stripped(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\nAAA\n")
    adapter = _EchoAdapter(path)
    result = adapter.tail()
    assert [t.symbol for t in result.trades] == ["AAA"]
    assert adapter.encrypted is True


def test_incomplete_magic_header_waits_for_more_bytes(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_bytes(LogSourceAdapter.MAGIC_HEADER[:4])
    adapter = _EchoAdapter(path)
    result = adapter.tail()
    assert result.trades == []

    with path.open("ab") as f:
        f.write(LogSourceAdapter.MAGIC_HEADER[4:] + b"\nAAA\n")
    result = adapter.tail()
    assert [t.symbol for t in result.trades] == ["AAA"]


def test_custom_decoder_seam_is_invoked_with_key_and_mode(tmp_path: Path):
    path = tmp_path / "log.txt"
    path.write_bytes(LogSourceAdapter.MAGIC_HEADER + b"\nZZZ\n")

    calls: list[tuple[bytes, object, bool]] = []

    def decoder(raw_line: bytes, key: object, encrypted: bool) -> bytes:
        calls.append((raw_line, key, encrypted))
        return raw_line

    adapter = _EchoAdapter(path, key=b"opaque-key", decoder=decoder)
    result = adapter.tail()

    assert [t.symbol for t in result.trades] == ["ZZZ"]
    assert calls == [(b"ZZZ", b"opaque-key", True)]
