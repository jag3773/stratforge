"""Projector: journals + health + rollups → read-only snapshots."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from viz.projector import project


KNOB_KEYS = (
    "strategy_id",
    "mode",
    "bar_minutes",
    "channel_period",
    "atr_stop_mult",
    "exit_mode",
    "max_concurrent",
    "max_alloc_pct",
    "rotate_when_full",
    "rotate_min_hold_bars",
    "account_type",
)


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def _journal(lines: list[dict]) -> str:
    return "".join(json.dumps(e) + "\n" for e in lines)


def _dna() -> dict:
    return {
        "strategy_id": "trend_rotation_25_max8_rotate",
        "mode": "breakout",
        "bar_minutes": 240,
        "channel_period": 60,
        "atr_stop_mult": 0.0,
        "cooldown_bars": 2,
        "exit_mode": "slow_ema",
        "max_concurrent": 8,
        "max_alloc_pct": 0.25,
        "rotate_when_full": True,
        "rotate_min_hold_bars": 14,
        "account_type": "CASH",
    }


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    cfg = root / "config"
    data = root / "data"
    results = root / "results" / "backtests" / "monthly_history_max8_rotate"
    cfg.mkdir(parents=True)
    data.mkdir(parents=True)
    _write(cfg / "trend_rotation_25_max8_rotate.json", _dna())
    _write(
        cfg / "live_paper.json",
        {"strategy_id": "trend_rotation_25_max8_rotate", "_include": "config/trend_rotation_25_max8_rotate.json", "start_usd": 250.0},
    )
    _write(
        cfg / "live_micro.json",
        {"strategy_id": "trend_rotation_25_max8_rotate", "_include": "config/trend_rotation_25_max8_rotate.json", "start_usd": 250.0},
    )
    _write(
        data / "execution_log.jsonl",
        _journal(
            [
                {"action": "OPENING_BALANCE", "capital": 250.0, "mode": "PAPER", "ts": "2026-08-16T01:00:00+00:00"},
                {"action": "CASH_DEPOSIT", "amount": 250.0, "cash": 500.0, "equity": 500.0, "ts": "2026-08-18T01:00:00+00:00"},
                {
                    "action": "PAPER_BUY",
                    "symbol": "SOL",
                    "qty": 1.0,
                    "price": 100.0,
                    "fee": 0.1,
                    "notional": 100.0,
                    "cash": 399.9,
                    "equity": 499.9,
                    "ts": "2026-08-19T16:00:00+00:00",
                },
            ]
        ),
    )
    _write(
        data / "node_health.json",
        {
            "equity": 648.31,
            "cash": 0.0,
            "mode": "paper",
            "strategy": "trend_rotation_25_max8_rotate",
            "positions": ["SOL", "LINK"],
            "n_positions": 2,
            "last_bar_close": "2026-08-26T20:00:00+00:00",
            "ts": "2026-08-27T00:01:00+00:00",
            "max_concurrent": 8,
            "rotate_when_full": True,
            "rotate_min_hold_bars": 14,
        },
    )
    _write(
        results / "rollup.json",
        [
            {"start": "2023-07-02", "return_pct": 403.72, "btc_return_pct": 110.32, "beats_btc": True, "open_n": 8},
            {"start": "2023-08-02", "return_pct": -16.4, "btc_return_pct": 10.0, "beats_btc": False, "open_n": 8},
            {"start": "2023-09-02", "return_pct": 192.5, "btc_return_pct": 50.0, "beats_btc": True, "open_n": 8},
        ],
    )
    return root


def test_paper_equity_comes_from_node_health_not_journal(tmp_path: Path):
    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    project(root=root, out=out)
    now = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert now["mode"] == "PAPER"
    assert now["equity"] == 648.31
    assert now["equity_source"] == "node_health"
    assert now["contributed"] == 500.0
    assert now["cash"] == 0.0
    assert "SOL" in [p["symbol"] for p in now["positions"]]


def test_identity_names_dna_file_and_knobs(tmp_path: Path):
    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    project(root=root, out=out)
    ident = json.loads((out / "identity.json").read_text())
    assert ident["config_path"] == "config/trend_rotation_25_max8_rotate.json"
    knobs = ident["knobs"]
    for k in KNOB_KEYS:
        assert k in knobs
    assert knobs["max_alloc_pct"] == 0.25
    assert knobs["max_concurrent"] == 8
    assert knobs["rotate_when_full"] is True
    assert knobs["rotate_min_hold_bars"] == 14
    assert knobs["atr_stop_mult"] == 0.0
    assert knobs["mode"] == "breakout"


def test_fills_do_not_invent_fees(tmp_path: Path):
    root = _mini_repo(tmp_path)
    live = root / "data" / "execution_log_live.jsonl"
    live.write_text(
        _journal(
            [
                {"action": "OPENING_BALANCE", "capital": 250.0, "mode": "LIVE", "ts": "2026-08-23T00:00:00+00:00"},
                {
                    "action": "LIVE_BUY",
                    "symbol": "PUMP",
                    "qty": 10.0,
                    "price": 1.0,
                    "notional": 10.0,
                    "ts": "2026-08-23T08:00:00+00:00",
                },
            ]
        )
    )
    (root / "data" / "node_health_live.json").write_text(
        json.dumps({"equity": 249.0, "cash": 240.0, "mode": "live", "strategy": "trend_rotation_25_max8_rotate", "positions": ["PUMP"], "ts": "2026-08-23T09:00:00+00:00"})
    )
    out = tmp_path / "snap"
    project(root=root, out=out)
    fills = json.loads((out / "books" / "live" / "fills.json").read_text())["fills"]
    buy = next(f for f in fills if f["action"] == "LIVE_BUY")
    assert "fee" not in buy or buy["fee"] is None
    assert "cost" not in buy or buy["cost"] is None
    now = json.loads((out / "books" / "live" / "now.json").read_text())
    assert now["mode"] == "LIVE"
    assert now["equity"] == 249.0
    assert now["equity_source"] == "node_health"


def test_run_library_summarizes_rollup(tmp_path: Path):
    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    project(root=root, out=out)
    run = json.loads((out / "runs" / "monthly_history_max8_rotate.json").read_text())
    assert run["role"] == "PRODUCTION"
    assert run["strategy_id"] == "trend_rotation_25_max8_rotate"
    assert run["n"] == 3
    assert run["avg_return_pct"] == pytest.approx((403.72 + -16.4 + 192.5) / 3)
    assert run["min_return_pct"] == -16.4
    assert run["max_return_pct"] == 403.72
    assert run["beats_btc"] == 2
    assert len(run["points"]) == 3
    idx = json.loads((out / "index.json").read_text())
    assert idx["books"][0]["id"] == "paper"
    assert any(r["id"] == "monthly_history_max8_rotate" for r in idx["runs"])
    assert "ET" in idx["as_of_et"]


def test_research_run_reads_knobs_sidecar(tmp_path: Path):
    root = _mini_repo(tmp_path)
    d = root / "results" / "backtests" / "donch_d20_10"
    _write(
        d / "rollup.json",
        [
            {"start": "2023-07-02", "return_pct": 1632.18, "btc_return_pct": 110.32, "beats_btc": True, "open_n": 8},
            {"start": "2025-10-02", "return_pct": 132.75, "btc_return_pct": -46.64, "beats_btc": True, "open_n": 8},
        ],
    )
    _write(
        d / "knobs.json",
        {
            "strategy_id": "donch_d20_10",
            "mode": "donchian",
            "bar_minutes": 1440,
            "channel_period": 20,
            "exit_mode": "donchian_10",
            "max_concurrent": 8,
            "max_alloc_pct": 0.25,
            "_config_path": "strategies/research_families.py",
        },
    )
    out = tmp_path / "snap"
    project(root=root, out=out)
    run = json.loads((out / "runs" / "donch_d20_10.json").read_text())
    assert run["role"] == "RESEARCH"
    assert run["strategy_id"] == "donch_d20_10"
    assert run["knobs"]["mode"] == "donchian"
    assert run["knobs"]["channel_period"] == 20
    assert "_config_path" not in run["knobs"]
    assert run["config_path"] == "strategies/research_families.py"
    assert run["n"] == 2


def test_missing_journal_skips_book(tmp_path: Path):
    root = _mini_repo(tmp_path)
    (root / "data" / "execution_log.jsonl").unlink()
    (root / "data" / "node_health.json").unlink()
    out = tmp_path / "snap"
    project(root=root, out=out)
    assert not (out / "books" / "paper" / "now.json").exists()
    idx = json.loads((out / "index.json").read_text())
    assert idx["books"] == []


def test_halt_flag_from_file_presence(tmp_path: Path):
    root = _mini_repo(tmp_path)
    (root / "data" / "TRADING_HALTED_paper").write_text("1")
    out = tmp_path / "snap"
    project(root=root, out=out)
    now = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert now["halted"] is True
    assert "TRADING_HALTED_paper" in now["halt_files"]


def test_last_bar_close_price_is_not_a_clock(tmp_path: Path):
    root = _mini_repo(tmp_path)
    health = json.loads((root / "data" / "node_health.json").read_text())
    health["last_bar_close"] = 707.61
    (root / "data" / "node_health.json").write_text(json.dumps(health))
    out = tmp_path / "snap"
    project(root=root, out=out)
    now = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert now["last_bar_close"] == 707.61
    assert now["last_bar_close_et"] == ""


def test_last_bar_close_iso_stays_eastern(tmp_path: Path):
    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    project(root=root, out=out)
    now = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert "ET" in now["last_bar_close_et"]
    assert "1969" not in now["last_bar_close_et"]


def test_sleeve_knob_diff_vs_dna(tmp_path: Path):
    root = _mini_repo(tmp_path)
    (root / "config" / "live_micro.json").write_text(
        json.dumps(
            {
                "strategy_id": "trend_rotation_25_max8_rotate",
                "_include": "config/trend_rotation_25_max8_rotate.json",
                "max_alloc_pct": 0.1,
                "start_usd": 250.0,
            }
        )
    )
    (root / "data" / "execution_log_live.jsonl").write_text(
        json.dumps({"action": "OPENING_BALANCE", "capital": 250.0, "mode": "LIVE", "ts": "2026-08-23T00:00:00+00:00"})
        + "\n"
    )
    (root / "data" / "node_health_live.json").write_text(
        json.dumps({"equity": 250.0, "cash": 250.0, "mode": "live", "ts": "2026-08-23T09:00:00+00:00"})
    )
    out = tmp_path / "snap"
    project(root=root, out=out)
    live = json.loads((out / "books" / "live" / "now.json").read_text())
    assert live["knobs"]["max_alloc_pct"] == 0.1
    assert live["knob_diff"]["max_alloc_pct"]["sleeve"] == 0.1
    assert live["knob_diff"]["max_alloc_pct"]["dna"] == 0.25
    paper = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert paper["knob_diff"] == {}
    ident = json.loads((out / "identity.json").read_text())
    assert ident["config_path"] == "config/trend_rotation_25_max8_rotate.json"
    assert ident["knobs"]["max_alloc_pct"] == 0.25


def test_equity_points_unique_per_unix_second():
    from viz.projector import _equity_points

    pts = _equity_points(
        [
            {"equity": 250.0, "ts": "2026-08-25T00:00:01.031060+00:00"},
            {"equity": 661.24, "ts": "2026-08-25T00:00:01.054281+00:00"},
            {"equity": 659.18, "ts": "2026-08-25T00:00:03.654447+00:00"},
        ]
    )
    assert [p["equity"] for p in pts] == [661.24, 659.18]


def test_chart_ends_on_node_health_not_stale_fill(tmp_path: Path):
    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    project(root=root, out=out)
    pts = json.loads((out / "books" / "paper" / "equity.json").read_text())["points"]
    now = json.loads((out / "books" / "paper" / "now.json").read_text())
    assert now["equity"] == 648.31
    assert pts[-1]["equity"] == 648.31
    assert pts[-1]["t"] == "2026-08-27T00:01:00+00:00"
    assert pts[-2]["equity"] == 499.9


def test_live_mark_same_second_replaces_fill():
    from viz.projector import _append_live_mark

    pts = [{"t": "2026-09-18T20:00:09.878206+00:00", "equity": 821.51}]
    out = _append_live_mark(
        pts,
        {"ts": "2026-09-18T20:00:09.100508+00:00", "equity": 869.82},
        869.82,
    )
    assert [p["equity"] for p in out] == [869.82]


def test_live_mark_skipped_when_already_matches():
    from viz.projector import _append_live_mark

    pts = [{"t": "2026-09-18T20:01:26+00:00", "equity": 869.82}]
    out = _append_live_mark(pts, {"ts": "2026-09-18T20:02:00+00:00"}, 869.82)
    assert out == pts



def test_main_cli_explicit_root_and_out(tmp_path: Path):
    """main() must write snapshots when --root and --out are supplied explicitly."""
    from viz.projector import main

    root = _mini_repo(tmp_path)
    out = tmp_path / "snap"
    rc = main(["--root", str(root), "--out", str(out)])
    assert rc == 0
    assert (out / "index.json").is_file()
    idx = json.loads((out / "index.json").read_text())
    assert idx["books"][0]["id"] == "paper"


def test_main_cli_default_out_resolves_via_state_root(tmp_path: Path, monkeypatch):
    """When --out is omitted, the default must come from state_root(), not __file__."""
    from viz.projector import main

    root = _mini_repo(tmp_path)
    monkeypatch.setenv("STRATFORGE_STATE", str(root))
    rc = main(["--root", str(root)])
    assert rc == 0
    default_out = root / "viz" / "snapshots"
    assert (default_out / "index.json").is_file()
