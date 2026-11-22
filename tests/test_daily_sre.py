#!/usr/bin/env python3
from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))


def test_deliverer(tmp_path: Path) -> None:
    import daily_sre_deliver as d

    report = tmp_path / "sre.md"
    d.REPORT = report
    d.MAX_AGE_SEC = 6 * 3600

    report.write_text("VERDICT: GREEN\n\nAll good.\n")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert d.main() == 0
    assert buf.getvalue().strip() == "✅ SRE check clean."

    report.write_text("VERDICT: YELLOW\n\nPaper feed stale.\n")
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert d.main() == 0
    assert "VERDICT: YELLOW" in buf.getvalue()

    d.REPORT = tmp_path / "missing.md"
    buf = io.StringIO()
    with redirect_stdout(buf):
        assert d.main() == 0
    assert "RED" in buf.getvalue()
    print("ok deliverer")


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_deliverer(Path(td))
    print("ALL DAILY SRE TESTS PASS")
