"""Viz web: GET-only boundary."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "viz" / "web"


def test_fetch_lives_only_in_load_ts():
    src = WEB / "src"
    load = src / "load.ts"
    assert load.is_file(), "viz/web/src/load.ts is the only allowed fetch site"
    text = load.read_text()
    assert "import.meta.env.BASE_URL" in text
    assert "fetch(" in text
    offenders = []
    for p in src.rglob("*"):
        if not p.is_file() or p.suffix not in {".ts", ".js", ".html"}:
            continue
        if p.name == "load.ts":
            continue
        text = p.read_text()
        if "fetch(" in text:
            offenders.append(str(p.relative_to(WEB)))
    assert offenders == []


def test_no_money_verbs_in_bundle_source():
    blob = ""
    for p in (WEB / "src").rglob("*"):
        if p.suffix in {".ts", ".js", ".html", ".css"}:
            blob += p.read_text()
    for needle in ("create_order", "BEGIN PRIVATE KEY", "unwrapSecret"):
        assert needle not in blob


def test_theme_toggle_without_tailwind():
    pkg = (WEB / "package.json").read_text()
    assert "tailwind" not in pkg.lower()
    html = (WEB / "index.html").read_text()
    assert 'id="theme"' in html
    assert "viz.theme" in html
    css = (WEB / "src" / "style.css").read_text()
    assert '[data-theme="light"]' in css
    assert "margin: 0 auto" in css


def test_metric_row_equal_cards_gap_to_chart():
    css = (WEB / "src" / "style.css").read_text()
    assert "align-items: stretch" in css
    assert "white-space: nowrap" in css
    assert "margin: 0 0 16px" in css
    main = (WEB / "src" / "main.ts").read_text()
    assert "signedMoney(now.pnl))} (${esc(pct" not in main
    assert "P&amp;L ${esc(pct(now.pnl_pct))}" in main


def test_chart_css_does_not_pad_lightweight_tables():
    css = (WEB / "src" / "style.css").read_text()
    assert "table.data" in css
    assert ".tv-lightweight-charts td" in css
    charts = (WEB / "src" / "charts.ts").read_text()
    assert "autoSize: true" in charts
    assert "handleScroll: false" in charts
    assert "strictly increasing" in charts


def test_book_caption_does_not_show_a_second_equity():
    main = (WEB / "src" / "main.ts").read_text()
    assert "disagree" not in main
    assert "journal marks" not in main
    assert "Fills, then live mark" in main
