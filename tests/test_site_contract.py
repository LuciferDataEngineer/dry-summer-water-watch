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


def test_each_tab_has_its_own_takeaway_and_cards():
    if not (SITE / "index.html").exists():
        pytest.skip("site has not been built")
    headings = []
    counts = []
    for name in PAGES:
        text = (SITE / name).read_text(encoding="utf-8")
        headings.append(re.search(r"<h1>(.*?)</h1>", text).group(1))
        counts.append(len(re.findall(r'class="kpi ', text)))
    assert len(set(headings)) == 4
    assert "town supply already under the 40% line" in headings[0]
    for heading in headings[1:]:
        assert "town supply already under the 40% line" not in heading
    assert counts[0] == 5
    for count in counts[1:]:
        assert 2 <= count <= 4
    assert "runs out first" in headings[1]
    assert "empty by the end of March" in headings[2]
    assert "gap" in headings[3]
    index = (SITE / "index.html").read_text(encoding="utf-8")
    assert "not a town supply" in index
    assert "Emptiest storage" in index


def test_under_40_count_uses_one_definition():
    if not (SITE / "index.html").exists():
        pytest.skip("site has not been built")
    index = (SITE / "index.html").read_text(encoding="utf-8")
    tight = (SITE / "tight.html").read_text(encoding="utf-8")
    sentence = re.search(
        r"\d+ of \d+ town, farm and regional supplies are under 40% of accessible capacity\. "
        r"Power-station lakes are not in this count\.",
        index,
    )
    assert sentence, "outlook is missing the shared under-40% definition"
    assert sentence.group(0) in tight
    count = int(sentence.group(0).split()[0])
    svg = re.search(r'class="chart tight-chart".*?</svg>', tight).group(0)
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert labels.count("Under the line") == count
    assert "Great Lake" not in svg
    assert "Great Lake" in tight


def test_gap_chart_drops_states_without_a_town_gap():
    if not (SITE / "levers.html").exists():
        pytest.skip("site has not been built")
    text = (SITE / "levers.html").read_text(encoding="utf-8")
    svg = re.search(r'class="chart lever-chart".*?</svg>', text)
    assert svg, "gap chart missing"
    chart = svg.group(0)
    assert "Australian Capital Territory" not in chart
    assert "New South Wales" not in chart
    assert "Western Australia" in chart
    labels = re.findall(r"<text[^>]*>([^<]*)</text>", chart)
    assert "0 GL" not in labels
    assert "Australian Capital Territory" in text
    assert "New South Wales" in text


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
