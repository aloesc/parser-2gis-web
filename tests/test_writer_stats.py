import csv
import json
import os

from parser_2gis.writer.models.contact_group import Contact
from parser_2gis.writer.stats import (RUBRICS_COLUMN, SITE_COLUMN, compute_stats, groups_by_label,
                                      has_site, rubric_group, stats_from_csv,
                                      top_rubric, top_rubrics, write_stats)

RUBRICS = {
    '1': {'code': '1', 'label': 'Top A', 'parentCode': '0'},
    '2': {'code': '2', 'label': 'Mid', 'parentCode': '1'},
    '3': {'code': '3', 'label': 'Leaf', 'parentCode': '2'},
    '4': {'code': '4', 'label': 'Top B', 'parentCode': '0'},
}


def test_top_rubric():
    assert top_rubric('3', RUBRICS) == 'Top A'
    assert top_rubric(1, RUBRICS) == 'Top A'
    assert top_rubric('999', RUBRICS) is None
    assert top_rubric('161') == 'Досуг / Развлечения / Общественное питание'
    assert top_rubrics(['3', '2', '4', 'x'], RUBRICS) == ['Top A', 'Top B']


def test_has_site():
    assert has_site([Contact(type='website', url='http://a.ru', value='a')])
    assert not has_site([Contact(type='phone', value='1'), Contact(type='vkontakte', url='http://vk.com/a', value='a')])
    assert not has_site([])


def test_compute_stats_sorted():
    stats = compute_stats([(['A'], False), (['A'], True), (['B'], False), (['A', 'B'], True), ([], False)])
    by = {s.rubric: s for s in stats}
    assert (by['A'].total, by['A'].without_site) == (3, 1)
    assert (by['B'].total, by['B'].without_site) == (2, 1)
    assert by['Без рубрики'].share_without_site == 1.0
    assert [s.rubric for s in stats] == ['Без рубрики', 'B', 'A']


def test_csv_roundtrip(tmp_path):
    src = tmp_path / 'in.csv'
    with open(src, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['Наименование', SITE_COLUMN, RUBRICS_COLUMN])
        w.writerow(['x', 'да', 'A; B'])
        w.writerow(['y', 'нет', 'A'])
    stats = stats_from_csv(str(src))
    assert [(s.rubric, s.total, s.without_site) for s in stats] == [('A', 2, 1), ('B', 1, 0)]
    out = str(tmp_path / 'stats.json')
    write_stats(stats, out)
    assert json.load(open(out, encoding='utf-8'))[0]['rubric'] == 'A'
    for ext in ('csv', 'xlsx'):
        p = str(tmp_path / f'stats.{ext}')
        write_stats(stats, p)
        assert os.path.getsize(p) > 0


def test_groups_and_leaf_stats():
    rubrics = dict(RUBRICS, **{'0': {'code': '0', 'label': None, 'parentCode': None},
                               '5': {'code': '5', 'label': 'Leaf', 'parentCode': '4'}})
    assert rubric_group('3', rubrics) == 'Mid'
    assert rubric_group('1', rubrics) == ''
    assert rubric_group('999', rubrics) == ''
    assert groups_by_label(rubrics)['Leaf'] == ''  # ambiguous
    assert groups_by_label(rubrics)['Mid'] == 'Top A'
    stats = compute_stats([(['L', ('M', 'G')], False), (['L'], True)], groups={'L': 'P'})
    assert [(s.rubric, s.group, s.total, s.without_site) for s in stats] == [('M', 'G', 1, 1), ('L', 'P', 2, 1)]
    assert compute_stats([([], False)])[0].group == ''


def _write_csv(tmp_path, items, crawl_rubric=None, **csv_opts):
    from parser_2gis.writer import get_writer
    from parser_2gis.writer.options import WriterOptions
    opts = WriterOptions(csv=dict(add_site_flag=True, add_rubric_codes=True, **csv_opts))
    path = str(tmp_path / 'out.csv')
    with get_writer(path, 'csv', opts) as w:
        w.crawl_rubric = crawl_rubric
        for it in items:
            w.write({'meta': {'code': 200}, 'result': {'items': [it]}})
    with open(path, encoding='utf-8-sig', newline='') as f:
        return path, list(csv.DictReader(f))


def _item(i, rubrics):
    return {'id': i, 'name': 'n' + i, 'type': 'branch', 'locale': 'ru_RU', 'rubrics': rubrics}


def test_writer_rubric_codes_columns(tmp_path):
    rubs = [{'id': '324', 'name': 'Организация похорон', 'kind': 'primary', 'short_id': 1},
            {'id': '325', 'name': 'Памятники', 'kind': 'additional', 'short_id': 2}]
    _, rows = _write_csv(tmp_path, [_item('1', rubs)], crawl_rubric='324')
    assert rows[0]['Рубрики'] == 'Организация похорон; Памятники'
    assert rows[0]['Коды рубрик'] == '324; 325'
    assert rows[0]['Рубрика обхода'] == '324'
    # default: columns absent
    from parser_2gis.writer.options import CSVOptions
    assert CSVOptions().add_rubric_codes is False


def test_stats_by_code_and_crawl_fallback(tmp_path):
    rubs = [{'id': '324', 'name': 'Организация похорон', 'kind': 'primary', 'short_id': 1}]
    path, rows = _write_csv(tmp_path, [_item('1', rubs), _item('2', [])], crawl_rubric='324')
    assert rows[1]['Рубрика обхода'] == '324' and rows[1]['Коды рубрик'] == ''
    stats = {s.rubric: s for s in stats_from_csv(path)}
    group = rubric_group('324')
    assert stats['Организация похорон'].group == group != ''
    # record without API rubrics goes to the crawl rubric (label from rubrics.json)
    from parser_2gis.writer.stats import rubric_label
    assert stats[rubric_label('324')].group == group
    assert 'Без рубрики' not in stats


def test_row_rubrics_positional():
    from parser_2gis.writer.stats import row_rubrics
    assert row_rubrics(['A', 'B'], ['3', '4'], rubrics=RUBRICS) == [('A', 'Mid'), ('B', '')]
    assert row_rubrics([], [], '3', rubrics=RUBRICS) == [('Leaf', 'Mid')]
    assert row_rubrics([], [], '', rubrics=RUBRICS) == []
    assert row_rubrics(['A'], [], rubrics=RUBRICS) == [('A', '')]


def test_stats_from_csv_without_code_columns_uses_names(tmp_path):
    src = tmp_path / 'old.csv'
    with open(src, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow([SITE_COLUMN, RUBRICS_COLUMN])
        w.writerow(['нет', 'Помощь в организации похорон'])
        w.writerow(['да', ''])
    stats = {s.rubric: s for s in stats_from_csv(str(src))}
    assert stats['Помощь в организации похорон'].group == rubric_group('324')
    assert stats['Без рубрики'].total == 1
