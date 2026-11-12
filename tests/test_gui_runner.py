from __future__ import annotations

import json

from parser_2gis.chrome.exceptions import ChromeCaptchaException
from parser_2gis.config import Configuration
from parser_2gis.gui.urls_generator import GeneratedUrls, crawl_rubric_codes
from parser_2gis.runner import gui as gui_module
from parser_2gis.runner.gui import GUIRunner

RUBRICS = {'1': {'label': 'G', 'children': ['2', '3']}, '2': {'label': 'B', 'children': []},
           '3': {'label': 'C', 'children': []}, '9': {'label': 'Z', 'children': []}}


def doc(i):
    return {'meta': {'code': 200}, 'result': {'items': [{'id': i}]}}


class FakeWriter:
    def __init__(self):
        self.docs = []
        self.crawl_rubric = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def write(self, d):
        self.docs.append(d)


class FakeParser:
    def __init__(self, url, runner_hook=None, fail=False):
        self.url, self.hook, self.fail, self.closed = url, runner_hook, fail, False

    def parse(self, writer):
        if self.hook:
            self.hook(self)
        if self.fail:
            raise ChromeCaptchaException()
        writer.write(doc(self.url.rsplit('/', 1)[1]))

    def close(self):
        self.closed = True


def patch(monkeypatch, parser_factory, stats_calls):
    writer = FakeWriter()
    monkeypatch.setattr(gui_module, 'get_writer', lambda *a, **k: writer)
    monkeypatch.setattr(gui_module, 'get_parser', lambda url, **k: parser_factory(url))
    monkeypatch.setattr(gui_module, 'load_rubrics', lambda: RUBRICS)
    monkeypatch.setattr(gui_module, 'write_run_stats', lambda *a: stats_calls.append(a))
    return writer


def run(runner):
    runner.start()
    runner.join(10)
    assert not runner.is_alive()


def test_generated_urls_backward_compatible():
    plain = GeneratedUrls(['u1', 'u2'])
    assert isinstance(plain, list) and plain == ['u1', 'u2'] and not plain.crawl
    assert not GeneratedUrls()  # like an empty list
    c = GeneratedUrls(cities=['moscow'], rubric_codes=['2'])
    assert c.crawl and c == [] and c.cities == ['moscow'] and c.rubric_codes == ['2']


def test_crawl_rubric_codes():
    assert crawl_rubric_codes({'code': '1'}, RUBRICS) == ['2', '3']
    assert crawl_rubric_codes({'code': '9'}, RUBRICS) == ['9']
    assert crawl_rubric_codes(None, RUBRICS) == ['2', '3', '9']


def test_gui_runner_crawl_mode(tmp_path, monkeypatch):
    out = str(tmp_path / 'out.csv')
    stats = []
    cfg = Configuration()
    writer = patch(monkeypatch, lambda u: FakeParser(u), stats)
    runner = GUIRunner([], out, 'csv', cfg, crawl_cities=['moscow'], crawl_rubrics=['2', '3'])
    run(runner)
    assert [d['result']['items'][0]['id'] for d in writer.docs] == ['2', '3']
    assert len(stats) == 1 and stats[0][0] == out
    saved = json.load(open(out + '.checkpoint.json', encoding='utf-8'))
    assert saved['done'] == [['moscow', '2'], ['moscow', '3']]
    # required columns are enabled only in the runner's copy
    assert not cfg.writer.csv.add_site_flag


def test_gui_runner_crawl_resume(tmp_path, monkeypatch):
    out = str(tmp_path / 'out.csv')
    with open(out + '.checkpoint.json', 'w', encoding='utf-8') as f:
        json.dump({'done': [['moscow', '2']], 'ids': ['2']}, f)
    urls = []
    patch(monkeypatch, lambda u: (urls.append(u), FakeParser(u))[1], [])
    run(GUIRunner([], out, 'csv', Configuration(), crawl_cities=['moscow'], crawl_rubrics=['2', '3']))
    assert urls == ['https://2gis.ru/moscow/search/x/rubricId/3']


def test_gui_runner_crawl_captcha(tmp_path, monkeypatch, caplog):
    out = str(tmp_path / 'out.csv')
    stats = []
    patch(monkeypatch, lambda u: FakeParser(u, fail=u.endswith('/3')), stats)
    errors = []
    monkeypatch.setattr(gui_module.logger, 'error', lambda msg, *a, **k: errors.append(msg % a))
    run(GUIRunner([], out, 'csv', Configuration(), crawl_cities=['moscow'], crawl_rubrics=['2', '3']))
    assert any('капчу' in e for e in errors)
    assert not stats
    assert json.load(open(out + '.checkpoint.json', encoding='utf-8'))['done'] == [['moscow', '2']]


def test_gui_runner_crawl_stop(tmp_path, monkeypatch):
    out = str(tmp_path / 'out.csv')
    stats = []
    parsers = []
    holder = {}

    def factory(u):
        p = FakeParser(u, runner_hook=lambda _p: holder['r'].stop() if u.endswith('/2') else None)
        parsers.append(p)
        return p

    patch(monkeypatch, factory, stats)
    errors = []
    monkeypatch.setattr(gui_module.logger, 'error', lambda msg, *a, **k: errors.append(msg))
    runner = GUIRunner([], out, 'csv', Configuration(), crawl_cities=['moscow'], crawl_rubrics=['2', '3'])
    holder['r'] = runner
    run(runner)
    assert len(parsers) == 1 and parsers[0].closed  # second rubric is not started
    assert not stats and not errors
    cp = json.load(open(out + '.checkpoint.json', encoding='utf-8'))
    assert cp['done'] == []  # interrupted rubric will be repeated


def test_gui_runner_url_mode_unchanged(tmp_path, monkeypatch):
    out = str(tmp_path / 'out.csv')
    stats = []
    writer = patch(monkeypatch, lambda u: FakeParser(u), stats)
    run(GUIRunner(['https://2gis.ru/a/1', 'https://2gis.ru/a/2'], out, 'csv', Configuration()))
    assert [d['result']['items'][0]['id'] for d in writer.docs] == ['1', '2']
    assert not stats
    assert not (tmp_path / 'out.csv.checkpoint.json').exists()
