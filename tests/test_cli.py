"""CLI: argument parsing and dry-run-by-default wiring (fetcher injected)."""

from __future__ import annotations

from nyc_executive_orders import cli


def test_harvest_requires_years():
    import pytest

    with pytest.raises(SystemExit):
        cli.build_arg_parser().parse_args(["harvest"])


def test_dry_run_is_default(monkeypatch, tmp_path, harvest_fetcher):
    # Replace the real fetcher builder so main() never touches the network.
    monkeypatch.setattr(cli, "build_fetcher", lambda backend: harvest_fetcher)

    rc = cli.main(
        [
            "harvest",
            "--from-year", "2024",
            "--to-year", "2024",
            "--delay", "0",
            "--page-size", "2",
            "--out-dir", str(tmp_path),
            "--pdf-dir", str(tmp_path / "pdfs"),
            "--index-dir", str(tmp_path / "index"),
        ]
    )
    assert rc == 0
    # Dry-run default -> no downloads attempted.
    assert harvest_fetcher.bytes_calls == []
    assert (tmp_path / "index" / "eo_index.json").exists()


def test_download_flag_enables_downloads(monkeypatch, tmp_path, harvest_fetcher):
    monkeypatch.setattr(cli, "build_fetcher", lambda backend: harvest_fetcher)

    rc = cli.main(
        [
            "harvest",
            "--from-year", "2024",
            "--to-year", "2024",
            "--download",
            "--delay", "0",
            "--page-size", "2",
            "--out-dir", str(tmp_path),
            "--pdf-dir", str(tmp_path / "pdfs"),
            "--index-dir", str(tmp_path / "index"),
        ]
    )
    assert rc == 0
    assert len(harvest_fetcher.bytes_calls) == 3
