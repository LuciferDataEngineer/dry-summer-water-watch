"""Browser checks for clipped or overlapping labels, and for KPI cards wrapping."""

import functools
import http.server
import threading
from pathlib import Path

import pytest

SITE = Path(__file__).resolve().parents[1] / "site"
PAGES = ("index.html", "tight.html", "scenarios.html", "levers.html")

pytest.importorskip("playwright.sync_api")

DESKTOP = ({"width": 1280, "height": 900}, {"width": 1100, "height": 900})
PHONE = {"width": 390, "height": 844}

_LABELS = """
() => {
  const problems = [];
  const overlaps = (a, b) => {
    const width = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const height = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return width > 1.5 && height > 1.5;
  };
  document.querySelectorAll('svg.chart').forEach(svg => {
    const svgBox = svg.getBoundingClientRect();
    const texts = [...svg.querySelectorAll('text')].filter(node => (node.textContent || '').trim());
    const boxes = texts.map(node => ({node, text: node.textContent.trim(), box: node.getBoundingClientRect()}));
    boxes.forEach(item => {
      const box = item.box;
      if (box.width < 0.5 || box.height < 0.5) {
        problems.push('blank "' + item.text + '"');
        return;
      }
      const pad = 1.5;
      if (box.left < svgBox.left - pad || box.right > svgBox.right + pad || box.top < svgBox.top - pad || box.bottom > svgBox.bottom + pad) {
        problems.push('clipped "' + item.text + '"');
      }
    });
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        if (overlaps(boxes[i].box, boxes[j].box)) {
          problems.push('overlap "' + boxes[i].text + '" / "' + boxes[j].text + '"');
        }
      }
    }
    boxes.forEach(item => {
      const box = item.box;
      const samples = [
        [box.left + box.width * 0.5, box.top + box.height * 0.55],
        [box.left + box.width * 0.32, box.top + box.height * 0.55],
        [box.right - box.width * 0.32, box.top + box.height * 0.55]
      ];
      let hits = 0;
      samples.forEach(([x, y]) => {
        const mark = document.elementsFromPoint(x, y).find(node => {
          if (node === item.node || item.node.contains(node)) return false;
          return node.closest && node.closest('svg') === svg && ['path', 'line', 'rect', 'polygon', 'circle'].includes(node.tagName.toLowerCase());
        });
        if (mark) hits += 1;
      });
      if (hits >= 2) problems.push('sits on a mark "' + item.text + '"');
    });
  });
  return problems;
}
"""


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A003
        return


@pytest.fixture(scope="module")
def site_url():
    handler = functools.partial(_QuietHandler, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        launched = playwright.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        yield launched
        launched.close()


def _open(browser, url, viewport):
    page = browser.new_page(viewport=viewport)
    page.goto(url, wait_until="domcontentloaded")
    page.evaluate("() => document.fonts.ready")
    return page


def _assert_labels(page, where):
    problems = page.evaluate(_LABELS)
    assert problems == [], f"{where}: {problems}"


def _kpi_rows(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('.kpis')].map(section => {
          const cards = [...section.querySelectorAll(':scope > .kpi')].map(card => {
            const box = card.getBoundingClientRect();
            return {top: box.top, bottom: box.bottom, left: box.left, right: box.right};
          });
          return cards;
        })"""
    )


def test_desktop_kpi_cards_stay_on_one_row_and_labels_fit(site_url, browser):
    for viewport in DESKTOP:
        for name in PAGES:
            page = _open(browser, f"{site_url}/{name}", viewport)
            try:
                rows = _kpi_rows(page)
                assert rows, name
                for cards in rows:
                    assert cards, name
                    assert max(card["top"] for card in cards) - min(card["top"] for card in cards) < 2, name
                    assert max(card["right"] for card in cards) <= viewport["width"] + 1
                _assert_labels(page, f"{name} {viewport['width']}")
                if name == "index.html":
                    state = page.evaluate(
                        """() => parseFloat(getComputedStyle(document.querySelector('.state-chart text')).fontSize)"""
                    )
                    assert state >= 14, state
            finally:
                page.close()
        tight = _open(browser, f"{site_url}/tight.html", viewport)
        try:
            tight_font = tight.evaluate(
                """() => parseFloat(getComputedStyle(document.querySelector('.tight-chart text')).fontSize)"""
            )
        finally:
            tight.close()
        outlook = _open(browser, f"{site_url}/index.html", viewport)
        try:
            state_font = outlook.evaluate(
                """() => parseFloat(getComputedStyle(document.querySelector('.state-chart text')).fontSize)"""
            )
        finally:
            outlook.close()
        scale = (state_font / 14) / (tight_font / 13)
        assert 0.85 <= scale <= 1.15, (state_font, tight_font, viewport)


def test_kpi_cards_stack_on_a_phone(site_url, browser):
    for name in PAGES:
        page = _open(browser, f"{site_url}/{name}", PHONE)
        try:
            for cards in _kpi_rows(page):
                assert len(cards) >= 2, name
                assert cards[1]["top"] >= cards[0]["bottom"] - 1, name
                assert cards[0]["right"] <= PHONE["width"] + 1
            _assert_labels(page, f"{name} phone")
        finally:
            page.close()


def test_scenario_labels_stay_clear_after_each_supply_is_chosen(site_url, browser):
    page = _open(browser, f"{site_url}/scenarios.html", DESKTOP[0])
    try:
        ids = page.eval_on_selector_all("button[data-id]", "nodes => nodes.map(node => node.dataset.id)")
        assert len(ids) >= 2
        for storage_id in ids:
            page.click(f'button[data-id="{storage_id}"]')
            page.wait_for_selector("#scenario-chart svg.scenario-chart")
            _assert_labels(page, storage_id)
            tops = [card["top"] for card in _kpi_rows(page)[0]]
            assert max(tops) - min(tops) < 2
    finally:
        page.close()


def test_tight_chart_labels_stay_clear_for_every_state_filter(site_url, browser):
    page = _open(browser, f"{site_url}/tight.html", DESKTOP[0])
    try:
        states = page.eval_on_selector_all("button[data-state]", "nodes => nodes.map(node => node.dataset.state)")
        assert "All" in states
        for state in states:
            page.click(f'button[data-state="{state}"]')
            page.wait_for_selector("#tight-chart svg.tight-chart")
            _assert_labels(page, state)
    finally:
        page.close()
