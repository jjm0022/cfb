"""Atomic file writes: a reader never sees half a file, and failures leave no litter."""

from pathlib import Path

import pytest

from pickem.atomic import write_atomic


def test_text_round_trips_as_utf8(tmp_path):
    target = tmp_path / "page.html"
    write_atomic(target, "café \U0001f525")
    assert target.read_bytes() == "café \U0001f525".encode()


def test_bytes_are_written_as_is(tmp_path):
    target = tmp_path / "logo.png"
    write_atomic(target, b"\x89PNG\r\n\x1a\n\x00\xff")
    assert target.read_bytes() == b"\x89PNG\r\n\x1a\n\x00\xff"


def test_replaces_an_existing_file(tmp_path):
    target = tmp_path / "page.html"
    target.write_text("old")
    write_atomic(target, "new")
    assert target.read_text() == "new"


def test_creates_the_parent_folder(tmp_path):
    target = tmp_path / "logos" / "nfl" / "BUF.png"
    write_atomic(target, b"x")
    assert target.read_bytes() == b"x"


def test_a_failed_rename_leaves_no_temp_file_and_the_original_alone(tmp_path, monkeypatch):
    target = tmp_path / "page.html"
    target.write_text("old")

    def boom(self, destination):
        raise OSError("disk gone")

    monkeypatch.setattr(Path, "replace", boom)
    with pytest.raises(OSError, match="disk gone"):
        write_atomic(target, "new")
    assert target.read_text() == "old"
    assert list(tmp_path.iterdir()) == [target]
