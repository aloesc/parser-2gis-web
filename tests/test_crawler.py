from __future__ import annotations

import json

import pytest

from parser_2gis.chrome.exceptions import ChromeCaptchaException
from parser_2gis.crawler import (Checkpoint, DedupWriter, build_tasks, crawl,
                                 leaf_rubrics, load_cities, load_rubrics, read_rubrics_file)
from parser_2gis.runner.crawler import prepare_resume

CITIES = {'msk': {'code': 'msk', 'domain': 'ru'}, 'dubai': {'code': 'dubai', 'domain': 'ae'}}


def doc(i):
    return {'meta': {'code': 200}, 'result': {'items': [{'id': i}]}}


class FakeWriter:
    def __init__(self):
        self.docs = []

    def write(self, d):
        self.docs.append(d)


class FakeParser:
    def __init__(self, url, data, fail_on=None):
        self.url, self.data, self.fail_on = url, data, fail_on

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def parse(self, writer):
        if self.fail_on and self.fail_on in self.url:
            raise ChromeCaptchaException()
        for i in self.data.get(self.url.rsplit('/', 1)[1], []):
            writer.write(doc(i))


def test_leaf_rubrics():
    rubrics = {'1': {'label': 'A', 'children': ['2', '3']}, '2': {'label': 'B'},
               '3': {'label': '', 'children': []}, '10': {'label': 'C', 'children': []}}
    assert leaf_rubrics(rubrics) == ['2', '10']


def test_real_data_leaf_rubrics():
    codes = leaf_rubrics(load_rubrics())
    assert codes and all(not load_rubrics()[c].get('children') for c in codes)
    assert 'moscow' in load_cities()


def test_build_tasks():
    tasks = build_tasks(['msk', 'dubai'], ['5', '6'], CITIES)
    assert len(tasks) == 4
    assert tasks[0].url == 'https://2gis.ru/msk/search/x/rubricId/5'
    assert tasks[3].url == 'https://2gis.ae/dubai/search/x/rubricId/6'
    with pytest.raises(ValueError):
        build_tasks(['nope'], ['5'], CITIES)


def test_read_rubrics_file(tmp_path):
    p = tmp_path / 'r.txt'
    p.write_text('5\n\n# c\n6 # x\n5\n', encoding='utf-8')
    assert read_rubrics_file(str(p)) == ['5', '6']


def test_dedup_writer():
    w = FakeWriter()
    d = DedupWriter(w, set())
    d.write(doc('a'))
    d.write(doc('a'))
    d.write(doc('b'))
    assert [x['result']['items'][0]['id'] for x in w.docs] == ['a', 'b']
    assert d.skipped == 1


def test_checkpoint_roundtrip(tmp_path):
    path = str(tmp_path / 'out.csv.checkpoint.json')
    cp = Checkpoint(path)
    assert not cp.load()
    cp.done.add(('msk', '5'))
    cp.ids.add('x')
    cp.save()
    assert not (tmp_path / 'out.csv.checkpoint.json.tmp').exists()
    cp2 = Checkpoint(path)
    assert cp2.load() and cp2.done == {('msk', '5')} and cp2.ids == {'x'}
    (tmp_path / 'out.csv.checkpoint.json').write_text('{bad', encoding='utf-8')
    assert not Checkpoint(path).load()


def test_crawl_dedup_and_resume(tmp_path):
    path = str(tmp_path / 'cp.json')
    data = {'5': ['a', 'b'], '6': ['b', 'c'], '7': ['d']}
    tasks = build_tasks(['msk'], ['5', '6', '7'], CITIES)
    w = FakeWriter()
    cp = Checkpoint(path)

    # captcha on third rubric: checkpoint keeps first two
    with pytest.raises(ChromeCaptchaException):
        crawl(tasks, w, lambda u: FakeParser(u, data, fail_on='/7'), cp)
    assert [x['result']['items'][0]['id'] for x in w.docs] == ['a', 'b', 'c']
    saved = json.load(open(path, encoding='utf-8'))
    assert saved['done'] == [['msk', '5'], ['msk', '6']]

    # resume: only rubric 7 is visited
    visited = []
    cp2 = Checkpoint(path)
    assert cp2.load()
    w2 = FakeWriter()

    def factory(u):
        visited.append(u)
        return FakeParser(u, data)

    crawl(tasks, w2, factory, cp2)
    assert visited == [tasks[2].url]
    assert [x['result']['items'][0]['id'] for x in w2.docs] == ['d']


def test_prepare_resume(tmp_path):
    out = tmp_path / 'out.csv'
    out.write_text('old')
    cp = Checkpoint(str(out) + '.checkpoint.json')
    cp.done.add(('msk', '5'))
    cp.save()

    fresh = Checkpoint(cp.path)
    prepare_resume(str(out), fresh, resume=True)
    assert fresh.done and not out.exists() and (tmp_path / 'out.csv.part1').exists()

    again = Checkpoint(cp.path)
    prepare_resume(str(out), again, resume=False)
    assert not again.done and not (tmp_path / 'out.csv.checkpoint.json').exists()


def test_merge_stats_and_write_run_stats(tmp_path):
    from parser_2gis.runner.crawler import merge_stats, write_run_stats
    from parser_2gis.writer.stats import RubricStat

    a = [RubricStat(rubric='Кафе', group='Питание', total=2, without_site=1, share_without_site=0.5)]
    b = [RubricStat(rubric='Кафе', group='Питание', total=2, without_site=2, share_without_site=1.0)]
    merged = merge_stats([a, b])
    assert merged[0].total == 4 and merged[0].without_site == 3 and merged[0].share_without_site == 0.75

    out = tmp_path / 'out.csv'
    out.write_text('Наименование,Рубрики,Есть сайт\nA,Кафе,да\nB,Кафе,нет\n', encoding='utf-8-sig')
    (tmp_path / 'out.csv.part1').write_text('Наименование,Рубрики,Есть сайт\nC,Кафе,нет\n', encoding='utf-8-sig')
    write_run_stats(str(out), 'csv', '; ', 'utf-8-sig')
    text = (tmp_path / 'out.csv.stats.csv').read_text(encoding='utf-8-sig')
    assert 'Кафе' in text and '3' in text


def test_crawl_sets_crawl_rubric(tmp_path):
    seen = []

    class W(FakeWriter):
        def write(self, d):
            seen.append(self.crawl_rubric)

    w = W()
    tasks = build_tasks(['msk'], ['5', '6'], CITIES)
    crawl(tasks, w, lambda u: FakeParser(u, {'5': ['a'], '6': ['b']}), Checkpoint(str(tmp_path / 'cp.json')))
    assert seen == ['5', '6']
    assert w.crawl_rubric is None


def test_descendant_leaf_rubrics():
    from parser_2gis.crawler import descendant_leaf_rubrics
    rubrics = {'1': {'label': 'G', 'children': ['2', '3']}, '2': {'label': 'B', 'children': ['4']},
               '3': {'label': 'C', 'children': []}, '4': {'label': 'D'}, '5': {'label': 'E'},
               '6': {'label': '', 'children': []}}
    assert descendant_leaf_rubrics(rubrics, '1') == ['3', '4']
    assert descendant_leaf_rubrics(rubrics, '2') == ['4']
    assert descendant_leaf_rubrics(rubrics, '5') == ['5']  # leaf is itself
    assert descendant_leaf_rubrics(rubrics, '6') == []
    assert descendant_leaf_rubrics(rubrics, 'nope') == []


def test_real_data_group_leaves():
    from parser_2gis.crawler import descendant_leaf_rubrics
    rubrics = load_rubrics()
    group = next(c for c, r in rubrics.items() if r.get('children'))
    codes = descendant_leaf_rubrics(rubrics, group)
    assert codes and set(codes) <= set(leaf_rubrics(rubrics))


def test_crawl_cancel_between_rubrics(tmp_path):
    path = str(tmp_path / 'cp.json')
    data = {'5': ['a'], '6': ['b'], '7': ['c']}
    tasks = build_tasks(['msk'], ['5', '6', '7'], CITIES)
    cp = Checkpoint(path)
    w = FakeWriter()
    stop = []

    def factory(u):
        if u.endswith('/6'):
            stop.append(1)  # "Stop" pressed while rubric 6 is parsed
        return FakeParser(u, data)

    assert crawl(tasks, w, factory, cp, should_stop=lambda: bool(stop)) is False
    # rubric 6 was interrupted, so it is not done and will be repeated; 7 is not visited
    assert json.load(open(path, encoding='utf-8'))['done'] == [['msk', '5']]
    assert [x['result']['items'][0]['id'] for x in w.docs] == ['a', 'b']

    # resume repeats rubric 6 without duplicates
    cp2 = Checkpoint(path)
    assert cp2.load()
    w2 = FakeWriter()
    assert crawl(tasks, w2, lambda u: FakeParser(u, data), cp2) is True
    assert [x['result']['items'][0]['id'] for x in w2.docs] == ['c']  # 'b' already in checkpoint ids


def test_crawl_cancel_before_start(tmp_path):
    cp = Checkpoint(str(tmp_path / 'cp.json'))
    tasks = build_tasks(['msk'], ['5'], CITIES)
    assert crawl(tasks, FakeWriter(), lambda u: 1 / 0, cp, should_stop=lambda: True) is False
    assert (tmp_path / 'cp.json').exists()


class FlakyParser(FakeParser):
    """Raises `TimeoutError` for URLs ending with `bad` rubric the first `fails` times."""
    calls: dict = {}

    def __init__(self, url, data, bad, fails):
        super().__init__(url, data)
        self.bad, self.fails = bad, fails

    def parse(self, writer):
        if self.url.endswith('/' + self.bad):
            n = FlakyParser.calls[self.url] = FlakyParser.calls.get(self.url, 0) + 1
            if n <= self.fails:
                raise TimeoutError('hang')
        super().parse(writer)


def _flaky(tmp_path, fails, **kw):
    FlakyParser.calls = {}
    data = {'5': ['a'], '6': ['b'], '7': ['c']}
    tasks = build_tasks(['msk'], ['5', '6', '7'], CITIES)
    cp = Checkpoint(str(tmp_path / 'cp.json'))
    w = FakeWriter()
    pauses = []
    res = crawl(tasks, w, lambda u: FlakyParser(u, data, '6', fails), cp, sleep=pauses.append, **kw)
    return res, cp, w, pauses


def test_crawl_retry_succeeds(tmp_path):
    res, cp, w, pauses = _flaky(tmp_path, 1)
    assert res is True
    assert pauses == [5.0]
    assert ('msk', '6') in cp.done and not cp.failed
    assert [x['result']['items'][0]['id'] for x in w.docs] == ['a', 'b', 'c']


def test_crawl_retries_exhausted(tmp_path):
    res, cp, w, pauses = _flaky(tmp_path, 99)
    assert res is True
    assert pauses == [5.0, 15.0]
    assert cp.failed == {('msk', '6')} and ('msk', '6') not in cp.done
    assert [x['result']['items'][0]['id'] for x in w.docs] == ['a', 'c']
    saved = json.load(open(cp.path, encoding='utf-8'))
    assert saved['failed'] == [['msk', '6']]

    # resume retries the failed rubric and clears the failure
    cp2 = Checkpoint(cp.path)
    assert cp2.load() and cp2.failed == {('msk', '6')}
    FlakyParser.calls = {}
    data = {'5': ['a'], '6': ['b'], '7': ['c']}
    tasks = build_tasks(['msk'], ['5', '6', '7'], CITIES)
    assert crawl(tasks, FakeWriter(), lambda u: FakeParser(u, data), cp2) is True
    assert not cp2.failed and ('msk', '6') in cp2.done


def test_crawl_cancel_during_retry_pause(tmp_path):
    FlakyParser.calls = {}
    stop = []
    data = {'5': ['a'], '6': ['b']}
    tasks = build_tasks(['msk'], ['5', '6'], CITIES)
    cp = Checkpoint(str(tmp_path / 'cp.json'))
    res = crawl(tasks, FakeWriter(), lambda u: FlakyParser(u, data, '6', 99), cp,
                should_stop=lambda: bool(stop), sleep=lambda s: stop.append(s))
    assert res is False
    assert not cp.failed and cp.done == {('msk', '5')}
    assert FlakyParser.calls['https://2gis.ru/msk/search/x/rubricId/6'] == 1


def test_crawl_captcha_not_retried_and_checkpoint_saved(tmp_path):
    cp = Checkpoint(str(tmp_path / 'cp.json'))
    tasks = build_tasks(['msk'], ['5', '6'], CITIES)
    pauses = []
    with pytest.raises(ChromeCaptchaException):
        crawl(tasks, FakeWriter(), lambda u: FakeParser(u, {'5': ['a']}, fail_on='/6'), cp, sleep=pauses.append)
    assert not pauses and not cp.failed
    assert json.load(open(cp.path, encoding='utf-8'))['done'] == [['msk', '5']]


def test_checkpoint_without_failed_field(tmp_path):
    path = tmp_path / 'cp.json'
    path.write_text(json.dumps({'done': [['msk', '5']], 'ids': ['a']}), encoding='utf-8')
    cp = Checkpoint(str(path))
    assert cp.load() and cp.done == {('msk', '5')} and cp.failed == set()


def test_dedup_ignores_per_request_hash_in_id():
    from parser_2gis.crawler.crawler import DedupWriter

    class W:
        def __init__(self):
            self.docs = []

        def write(self, doc):
            self.docs.append(doc)

    def doc(i):
        return {'result': {'items': [{'id': i}]}}

    w, seen = W(), set()
    d = DedupWriter(w, seen)
    d.write(doc('2956015536441104_AAA'))
    d.write(doc('2956015536441104_BBB'))   # same firm, other request hash
    d.write(doc('2956015536441195_CCC'))   # another firm
    assert len(w.docs) == 2 and d.skipped == 1 and seen == {'2956015536441104', '2956015536441195'}


def test_checkpoint_normalizes_old_full_ids(tmp_path):
    import json
    from parser_2gis.crawler.crawler import Checkpoint

    path = tmp_path / 'cp.json'
    path.write_text(json.dumps({'done': [], 'ids': ['111_aaa', '111_bbb', '222_c']}), encoding='utf-8')
    cp = Checkpoint(str(path))
    assert cp.load() and cp.ids == {'111', '222'}
