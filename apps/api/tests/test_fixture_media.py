from pathlib import Path
import subprocess

import pytest

import fixture_media


def test_fixture_generation_times_out_and_removes_partial_outputs(
    tmp_path, monkeypatch
):
    """Would fail if a hung FFmpeg were unbounded or left a partial fixture behind."""
    observed_timeouts: list[float | None] = []

    def time_out(command, **kwargs):
        observed_timeouts.append(kwargs.get("timeout"))
        Path(command[-1]).write_bytes(b"partial fixture")
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(fixture_media.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(fixture_media.subprocess, "run", time_out)
    root = tmp_path / "timed-out-fixture"

    with pytest.raises(
        RuntimeError,
        match="^FFmpeg fixture generation timed out after 30 seconds$",
    ):
        fixture_media.generate_fixture_media(root)

    assert observed_timeouts == [30]
    assert list(root.iterdir()) == []
