import re
from pathlib import Path

import pytest

from waterwatch.config import DASHBOARD_BANNED

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
PAGES = ("index.html", "tight.html", "scenarios.html", "levers.html")


def _visible(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).lower()


def test_pages_exist_and_stay_in_plain_language():
    if not (SITE / "index.html").exists():
        pytest.skip("site has not been built")
    for name in PAGES:
        text = (SITE / name).read_text(encoding="utf-8")
        visible = _visible(text)
        for word in DASHBOARD_BANNED:
            assert re.search(rf"\b{re.escape(word)}\b", visible) is None, word
        assert "Exact values" in text
        assert "What this means" in text
        assert "AEDT" in text or "Australian Eastern" in text
        assert 'class="tab' in text
        assert "<svg" in text


def test_outlook_has_kpi_cards_and_a_map():
    if not (SITE / "index.html").exists():
        pytest.skip("site has not been built")
    text = (SITE / "index.html").read_text(encoding="utf-8")
    assert text.count('class="kpi') >= 4
    assert text.count("<circle") >= 5
    assert "40%" in text


def test_filters_and_charts_are_labelled():
    if not (SITE / "tight.html").exists():
        pytest.skip("site has not been built")
    tight = (SITE / "tight.html").read_text(encoding="utf-8")
    assert 'class="pill' in tight
    assert "<rect" in tight
    scenarios = (SITE / "scenarios.html").read_text(encoding="utf-8")
    assert "40% line" in scenarios
    assert "<path" in scenarios or "Not enough daily readings" in scenarios
    levers = (SITE / "levers.html").read_text(encoding="utf-8")
    assert "billion litres" in levers
    assert "<table" in levers
