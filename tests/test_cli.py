import json

import pytest

from marketalyzer.backtest.cli import grid_values, main

RANGE = ["--start", "2024-01-02", "--end", "2025-02-21"]
OFFLINE = ["--no-benchmark", "--no-usd"]


@pytest.fixture
def thyao(fake_fetch, prices, as_rows):
    fake_fetch.responses[("equity", "THYAO")] = as_rows(prices)
    return fake_fetch


def test_grid_values():
    assert grid_values("5:20:5") == [5, 10, 15, 20]
    assert grid_values("10,20") == [10, 20]
    assert grid_values("0.1:0.3:0.1") == pytest.approx([0.1, 0.2, 0.3])


@pytest.mark.parametrize("spec", ["5:20", "5:20:0", "a:b:c"])
def test_grid_values_rejects_bad_ranges(spec):
    with pytest.raises(Exception, match="START:STOP:STEP|STEP must be positive"):
        grid_values(spec)


def test_cli_json(thyao, capsys):
    code = main(["THYAO", *RANGE, *OFFLINE, "-p", "fast=5", "-p", "slow=20", "--json"])
    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["symbol"] == "THYAO"
    assert summary["params"] == {"fast": 5, "slow": 20}


def test_cli_table(thyao, capsys):
    assert main(["THYAO", *RANGE, *OFFLINE, "--commission", "0.001"]) == 0
    out = capsys.readouterr().out
    assert "Getiri [%]" in out
    assert "Toplam komisyon [TL]" in out


def test_cli_optimize(thyao, capsys):
    code = main(
        [
            "THYAO",
            *RANGE,
            *OFFLINE,
            "--optimize",
            "--grid",
            "fast=5,10",
            "--grid",
            "slow=20:40:20",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "En iyi parametreler" in out
    assert "Test dönemi" in out


def test_cli_plot(thyao, capsys, tmp_path):
    target = tmp_path / "plot.html"
    assert main(["THYAO", *RANGE, *OFFLINE, "--plot", str(target)]) == 0
    assert target.exists()


def test_cli_reports_errors(fake_fetch, capsys):
    assert main(["NOPE", *RANGE, *OFFLINE]) == 1
    assert "Hata: No data for NOPE" in capsys.readouterr().err
