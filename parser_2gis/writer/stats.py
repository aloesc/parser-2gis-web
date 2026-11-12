from __future__ import annotations

import csv
import json
import os
from functools import lru_cache
from typing import Any, Iterable, Mapping, Optional

from pydantic import BaseModel

RUBRICS_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'rubrics.json')

SITE_COLUMN = 'Есть сайт'
TOP_RUBRIC_COLUMN = 'Рубрика верхнего уровня'
RUBRICS_COLUMN = 'Рубрики'
RUBRIC_CODES_COLUMN = 'Коды рубрик'
CRAWL_RUBRIC_COLUMN = 'Рубрика обхода'
YES, NO = 'да', 'нет'


@lru_cache(maxsize=1)
def _load_rubrics() -> dict[str, Any]:
    with open(RUBRICS_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)  # type: ignore


def top_rubric(rubric_id: Any, rubrics: Optional[Mapping[str, Any]] = None) -> Optional[str]:
    """Return label of the top-level rubric (the one whose `parentCode` is "0") for `rubric_id`.

    Returns None for unknown rubrics.
    """
    if rubrics is None:
        rubrics = _load_rubrics()
    current = rubrics.get(str(rubric_id))
    seen: set[str] = set()
    while current is not None:
        code = str(current.get('code'))
        parent = str(current.get('parentCode', '0'))
        if parent in ('0', '') or parent in seen or parent not in rubrics:
            return current.get('label') or None
        seen.add(code)
        current = rubrics[parent]
    return None


def top_rubrics(rubric_ids: Iterable[Any], rubrics: Optional[Mapping[str, Any]] = None) -> list[str]:
    """Unique top-level rubric labels for several rubric ids (order of first appearance, unknown skipped)."""
    result: list[str] = []
    for rid in rubric_ids:
        label = top_rubric(rid, rubrics)
        if label and label not in result:
            result.append(label)
    return result


def has_site(contacts: Iterable[Any]) -> bool:
    """True if any contact has type `website` and a non-empty url."""
    return any(getattr(c, 'type', None) == 'website' and getattr(c, 'url', None) for c in contacts)


def rubric_group(rubric_id: Any, rubrics: Optional[Mapping[str, Any]] = None) -> str:
    """Label of the direct parent of `rubric_id`; empty string if unknown, top-level or label is None."""
    if rubrics is None:
        rubrics = _load_rubrics()
    node = rubrics.get(str(rubric_id))
    if node is None:
        return ''
    parent = rubrics.get(str(node.get('parentCode')))
    return (parent.get('label') or '') if parent else ''


@lru_cache(maxsize=1)
def _groups_by_label() -> dict[str, str]:
    return groups_by_label(_load_rubrics())


def groups_by_label(rubrics: Mapping[str, Any]) -> dict[str, str]:
    """Map rubric label -> group (parent label). Ambiguous labels (different groups) map to ''."""
    found: dict[str, set[str]] = {}
    for node in rubrics.values():
        label = node.get('label')
        if label:
            found.setdefault(label, set()).add(rubric_group(node.get('code'), rubrics))
    return {k: (next(iter(v)) if len(v) == 1 else '') for k, v in found.items()}


class RubricStat(BaseModel):
    rubric: str
    group: str = ''
    total: int
    without_site: int
    share_without_site: float


NO_RUBRIC = 'Без рубрики'


def compute_stats(records: Iterable[tuple[Iterable[Any], bool]],
                  groups: Optional[Mapping[str, str]] = None) -> list[RubricStat]:
    """Compute per-rubric statistics (rubrics are used as given, normally leaf rubrics).

    Args:
        records: Iterable of `(rubrics, has_site)`. Each rubric is a name or a `(name, group)` tuple.
            A record with several rubrics is counted once in EACH of them. Records without rubrics
            are counted under the bucket "Без рубрики".
        groups: Optional mapping rubric name -> group, used when the group is not given in the record.
            By default groups are looked up by label in `data/rubrics.json` (ambiguous -> empty).

    Returns:
        Stats sorted by share without site (descending), then total (descending), then name.
    """
    if groups is None:
        groups = _groups_by_label()
    acc: dict[str, list[int]] = {}
    grp: dict[str, str] = {}
    for labels, site in records:
        seen: dict[str, str] = {}
        for item in labels:
            name, g = item if isinstance(item, tuple) else (item, groups.get(item, ''))
            if name:
                seen.setdefault(name, g or '')
        for name, g in (seen or {NO_RUBRIC: ''}).items():
            s = acc.setdefault(name, [0, 0])
            s[0] += 1
            if not site:
                s[1] += 1
            if g and not grp.get(name):
                grp[name] = g
    stats = [RubricStat(rubric=k, group=grp.get(k, ''), total=t, without_site=w, share_without_site=round(w / t, 4))
             for k, (t, w) in acc.items()]
    stats.sort(key=lambda x: (-x.share_without_site, -x.total, x.rubric))
    return stats


def rubric_label(rubric_id: Any, rubrics: Optional[Mapping[str, Any]] = None) -> str:
    """Label of the rubric from `data/rubrics.json`; empty string if unknown."""
    if rubrics is None:
        rubrics = _load_rubrics()
    node = rubrics.get(str(rubric_id))
    return (node.get('label') or '') if node else ''


def row_rubrics(names: list[str], codes: list[str], crawl_code: str = '',
                rubrics: Optional[Mapping[str, Any]] = None) -> list[Any]:
    """Rubrics of one record as `(name, group)` tuples.

    Names (from API) are matched with codes by position, the group is found by code. Extra names
    without a code get no group, extra codes get the label from `rubrics.json`. A record without
    API rubrics falls back to the crawl rubric.
    """
    result: list[Any] = []
    for i in range(max(len(names), len(codes))):
        code = codes[i] if i < len(codes) else ''
        name = names[i] if i < len(names) else rubric_label(code, rubrics)
        if name:
            result.append((name, rubric_group(code, rubrics) if code else ''))
    if not result and crawl_code:
        name = rubric_label(crawl_code, rubrics)
        if name:
            result.append((name, rubric_group(crawl_code, rubrics)))
    return result


def stats_from_csv(csv_path: str, join_char: str = '; ', encoding: str = 'utf-8-sig') -> list[RubricStat]:
    """Compute stats from a CSV with `add_site_flag` and `add_rubrics` enabled (column "Рубрики").

    If columns "Коды рубрик" / "Рубрика обхода" exist, groups are found by code and records without
    API rubrics belong to the crawl rubric. Otherwise groups are found by rubric name.
    Falls back to the top-level rubric column if "Рубрики" is absent.
    """
    def split(value: Optional[str]) -> list[str]:
        return [x for x in (value or '').split(join_char) if x]

    def rows() -> Iterable[tuple[list[Any], bool]]:
        with open(csv_path, 'r', encoding=encoding, newline='') as f:
            reader = csv.DictReader(f)
            with_codes = RUBRIC_CODES_COLUMN in (reader.fieldnames or [])
            for row in reader:
                value = row.get(RUBRICS_COLUMN)
                if value is None:
                    value = row.get(TOP_RUBRIC_COLUMN) or ''
                if with_codes:
                    items = row_rubrics(split(value), split(row.get(RUBRIC_CODES_COLUMN)),
                                        (row.get(CRAWL_RUBRIC_COLUMN) or '').strip())
                else:
                    items = split(value)
                yield items, (row.get(SITE_COLUMN) == YES)

    return compute_stats(rows())


HEADER = ['Рубрика', 'Группа', 'Всего', 'Без сайта', 'Доля без сайта']


def write_stats(stats: list[RubricStat], path: str, encoding: str = 'utf-8-sig') -> None:
    """Write stats to `path`; format by extension: .json, .xlsx (own file), otherwise CSV."""
    ext = os.path.splitext(path)[1].lower()
    if ext == '.json':
        with open(path, 'w', encoding='utf-8') as f:
            json.dump([s.dict() for s in stats], f, ensure_ascii=False, indent=2)
    elif ext == '.xlsx':
        from xlsxwriter.workbook import Workbook
        with Workbook(path) as wb:
            add_stats_sheet(wb, stats)
    else:
        with open(path, 'w', encoding=encoding, newline='') as f:
            w = csv.writer(f)
            w.writerow(HEADER)
            for s in stats:
                w.writerow([s.rubric, s.group, s.total, s.without_site, s.share_without_site])


def add_stats_sheet(workbook: Any, stats: list[RubricStat], name: str = 'Статистика') -> None:
    """Add a stats worksheet to an xlsxwriter Workbook."""
    ws = workbook.add_worksheet(name)
    bold = workbook.add_format({'bold': True})
    pct = workbook.add_format({'num_format': '0.0%'})
    for c, h in enumerate(HEADER):
        ws.write(0, c, h, bold)
    for r, s in enumerate(stats, 1):
        ws.write(r, 0, s.rubric)
        ws.write(r, 1, s.group)
        ws.write(r, 2, s.total)
        ws.write(r, 3, s.without_site)
        ws.write(r, 4, s.share_without_site, pct)
