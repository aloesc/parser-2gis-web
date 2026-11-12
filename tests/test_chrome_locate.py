from __future__ import annotations

import os

import pytest

from parser_2gis.chrome import utils


@pytest.fixture(autouse=True)
def linux(monkeypatch):
    """Pretend to be Linux with an empty system and no cache between tests."""
    monkeypatch.setattr(utils, 'running_windows', lambda: False)
    monkeypatch.setattr(utils, 'running_mac', lambda: False)
    monkeypatch.setattr(utils.shutil, 'which', lambda name: None)
    monkeypatch.setattr(utils.glob, 'glob', lambda pattern: [])
    monkeypatch.setattr(utils.os.path, 'isfile', lambda path: False)
    utils.locate_chrome_path.cache_clear()
    yield
    utils.locate_chrome_path.cache_clear()


def have(monkeypatch, *paths):
    monkeypatch.setattr(utils.os.path, 'isfile', lambda path: path in paths)


def test_nothing_found():
    assert utils.locate_chrome_path() is None


def test_chromium_is_found(monkeypatch):
    have(monkeypatch, '/usr/bin/chromium')
    assert utils.locate_chrome_path() == '/usr/bin/chromium'


def test_chromium_browser_name_and_snap(monkeypatch):
    have(monkeypatch, '/snap/bin/chromium')
    assert utils.locate_chrome_path() == '/snap/bin/chromium'


def test_chrome_has_priority_over_chromium(monkeypatch):
    have(monkeypatch, '/usr/bin/chromium', '/usr/local/bin/google-chrome-stable')
    assert utils.locate_chrome_path() == '/usr/local/bin/google-chrome-stable'


def test_found_via_path_lookup(monkeypatch):
    monkeypatch.setattr(utils.shutil, 'which', lambda name: '/home/u/bin/chromium' if name == 'chromium' else None)
    have(monkeypatch, '/home/u/bin/chromium')
    assert utils.locate_chrome_path() == '/home/u/bin/chromium'


def test_playwright_chromium_is_the_last_resort_and_newest_wins(monkeypatch):
    root = os.path.join(os.sep, 'fake', 'ms-playwright')
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', root)
    old = os.path.join(root, 'chromium-900', 'chrome-linux', 'chrome')
    new = os.path.join(root, 'chromium-1254', 'chrome-linux64', 'chrome')
    monkeypatch.setattr(utils.glob, 'glob', lambda pattern: [old, new])
    have(monkeypatch, old, new)
    assert utils.locate_chrome_path() == new

    # system Chromium still wins over the Playwright one
    utils.locate_chrome_path.cache_clear()
    have(monkeypatch, old, new, '/usr/bin/chromium')
    assert utils.locate_chrome_path() == '/usr/bin/chromium'


def test_mac_chromium(monkeypatch):
    monkeypatch.setattr(utils, 'running_mac', lambda: True)
    have(monkeypatch, '/Applications/Chromium.app/Contents/MacOS/Chromium')
    assert utils.locate_chrome_path() == '/Applications/Chromium.app/Contents/MacOS/Chromium'
