from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from ..chrome.exceptions import ChromeCaptchaException, ChromeRuntimeException, ChromeUserAbortException
from ..logger import logger

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')


class CrawlTask(NamedTuple):
    city: str
    rubric: str
    url: str


def load_cities(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Return cities from `cities.json` as `{code: city}`."""
    with open(path or os.path.join(DATA_DIR, 'cities.json'), encoding='utf-8') as f:
        return {x['code']: x for x in json.load(f)}


def load_rubrics(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Return rubrics from `rubrics.json` as `{code: rubric}`."""
    with open(path or os.path.join(DATA_DIR, 'rubrics.json'), encoding='utf-8') as f:
        return json.load(f)


def leaf_rubrics(rubrics: Dict[str, Dict[str, Any]]) -> List[str]:
    """Codes of rubrics without children and with non-empty label."""
    codes = [code for code, r in rubrics.items() if not r.get('children') and r.get('label')]
    return sorted(codes, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))


def descendant_leaf_rubrics(rubrics: Dict[str, Dict[str, Any]], code: str) -> List[str]:
    """Codes of leaf rubrics inside the `code` node (the node itself for a leaf).
    Unknown code gives an empty list."""
    if code not in rubrics:
        return []

    result: set[str] = set()
    visited: set[str] = set()
    stack = [code]
    while stack:
        current = stack.pop()
        if current in visited or current not in rubrics:
            continue
        visited.add(current)
        node = rubrics[current]
        children = node.get('children')
        if children:
            stack.extend(children)
        elif node.get('label'):
            result.add(current)
    return sorted(result, key=lambda x: (not x.isdigit(), int(x) if x.isdigit() else 0, x))


def read_rubrics_file(path: str) -> List[str]:
    """Read rubric codes, one per line. Empty lines and `#` comments are ignored."""
    codes: List[str] = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            code = line.split('#', 1)[0].strip()
            if code and code not in codes:
                codes.append(code)
    return codes


def build_tasks(cities: List[str], rubric_codes: List[str],
                all_cities: Optional[Dict[str, Dict[str, Any]]] = None) -> List[CrawlTask]:
    """Build city x rubric list of search URLs.

    Raises:
        ValueError: Unknown city code.
    """
    all_cities = all_cities if all_cities is not None else load_cities()
    unknown = [c for c in cities if c not in all_cities]
    if unknown:
        raise ValueError('Неизвестные коды городов: %s' % ', '.join(unknown))

    tasks = []
    for city in cities:
        domain = all_cities[city]['domain']
        for rubric in rubric_codes:
            tasks.append(CrawlTask(city, rubric, f'https://2gis.{domain}/{city}/search/x/rubricId/{rubric}'))
    return tasks


def checkpoint_path(output_path: str) -> str:
    return output_path + '.checkpoint.json'


class Checkpoint:
    """Crawl progress: finished (city, rubric) pairs and seen record ids."""
    def __init__(self, path: str) -> None:
        self.path = path
        self.done: set[tuple[str, str]] = set()
        self.ids: set[str] = set()
        self.failed: set[tuple[str, str]] = set()

    def load(self) -> bool:
        """Load checkpoint file. Returns `False` if it doesn't exist or is broken."""
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            self.done = {(c, r) for c, r in data['done']}
            self.ids = {normalize_id(i) for i in data['ids']}  # old checkpoints kept full ids
            self.failed = {(c, r) for c, r in data.get('failed', [])} - self.done
            return True
        except FileNotFoundError:
            return False
        except (ValueError, KeyError, TypeError):
            logger.warning('Файл чекпоинта повреждён, обход начнётся заново: %s', self.path)
            self.done, self.ids, self.failed = set(), set(), set()
            return False

    def save(self) -> None:
        """Atomically write checkpoint (tmp file + rename)."""
        tmp = self.path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'done': sorted(self.done), 'ids': sorted(self.ids), 'failed': sorted(self.failed)}, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def remove(self) -> None:
        for p in (self.path, self.path + '.tmp'):
            if os.path.exists(p):
                os.remove(p)


def normalize_id(record_id: Any) -> str:
    """Stable part of an API id. 2GIS returns `<firm id>_<hash>` and the hash is different
    for every request, so ids of one firm from different rubrics don't match as a whole."""
    return str(record_id).split('_', 1)[0]


class DedupWriter:
    """Thin filter over a writer: skips records whose API id was already written."""
    def __init__(self, writer: Any, seen_ids: set[str]) -> None:
        self._writer = writer
        self.seen_ids = seen_ids
        self.skipped = 0

    @staticmethod
    def record_id(catalog_doc: Any) -> Optional[str]:
        try:
            return normalize_id(catalog_doc['result']['items'][0]['id'])
        except (KeyError, IndexError, TypeError):
            return None

    def write(self, catalog_doc: Any) -> None:
        record_id = self.record_id(catalog_doc)
        if record_id is not None:
            if record_id in self.seen_ids:
                self.skipped += 1
                return
            self.seen_ids.add(record_id)
        self._writer.write(catalog_doc)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)


RETRY_PAUSES = (5.0, 15.0)


def _interruptible_sleep(seconds: float, should_stop: Optional[Callable[[], bool]]) -> None:
    end = time.monotonic() + seconds
    while not (should_stop and should_stop()):
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


def crawl(tasks: List[CrawlTask], writer: Any, parser_factory: Callable[[str], Any],
          checkpoint: Checkpoint, should_stop: Optional[Callable[[], bool]] = None,
          on_progress: Optional[Callable[[int, int, int], None]] = None,
          sleep: Optional[Callable[[float], None]] = None,
          retry_pauses: tuple = RETRY_PAUSES) -> bool:
    """Sequentially parse tasks not yet done. Checkpoint is saved after every rubric
    and on any exception (including captcha), which is then re-raised.

    Args:
        parser_factory: `url -> parser` context manager with `.parse(writer)`.
        should_stop: Cancellation callback checked between rubrics. A rubric that
            was interrupted by cancellation is not marked as done.
        on_progress: Optional `(done, total, records)` callback: called before the first task
            and after every finished one (`records` = unique record ids seen so far).

        sleep: `seconds -> None` pause between retries (cancellation is checked right after it);
            by default sleeps in small steps and wakes up on `should_stop`.
        retry_pauses: Pauses before each retry of a failed rubric, so `len` is the retry count.
            A rubric that still fails goes to `checkpoint.failed` (not `done`) and the crawl goes on.
            Captcha, user abort and cancellation are never retried.

    Returns:
        `False` if the crawl was cancelled, `True` if all tasks are processed.
    """
    dedup = DedupWriter(writer, checkpoint.ids)
    total = len(tasks)
    done_count = sum(1 for t in tasks if (t.city, t.rubric) in checkpoint.done)

    def report() -> None:
        if on_progress:
            on_progress(done_count, total, len(checkpoint.ids))

    report()
    try:
        for n, task in enumerate(tasks, 1):
            if (task.city, task.rubric) in checkpoint.done:
                continue
            if should_stop and should_stop():
                logger.info('Обход остановлен, прогресс сохранён в %s', checkpoint.path)
                return False
            logger.info('Обход [%d/%d]: город %s, рубрика %s', n, total, task.city, task.rubric)
            writer.crawl_rubric = task.rubric  # read by CSVWriter ("Рубрика обхода")
            checkpoint.failed.discard((task.city, task.rubric))
            attempt = 0
            while True:
                try:
                    with parser_factory(task.url) as parser:
                        parser.parse(dedup)
                    break
                except (ChromeCaptchaException, ChromeUserAbortException, KeyboardInterrupt):
                    raise
                except Exception as e:
                    if (should_stop and should_stop()) or (
                            isinstance(e, ChromeRuntimeException) and str(e) == 'Tab has been stopped'):
                        raise
                    reason = '%s: %s' % (type(e).__name__, str(e)[:200])
                    if attempt >= len(retry_pauses):
                        logger.error('Рубрика %s (город %s) не обработана после %d попыток (%s), пропускаем.',
                                     task.rubric, task.city, attempt + 1, reason)
                        checkpoint.failed.add((task.city, task.rubric))
                        break
                    pause = retry_pauses[attempt]
                    attempt += 1
                    logger.warning('Сбой рубрики %s (город %s): %s. Повтор %d из %d через %g с.',
                                   task.rubric, task.city, reason, attempt, len(retry_pauses), pause)
                    if sleep:
                        sleep(pause)
                    else:
                        _interruptible_sleep(pause, should_stop)
                    if should_stop and should_stop():
                        logger.info('Обход остановлен, прогресс сохранён в %s', checkpoint.path)
                        return False
            if (task.city, task.rubric) in checkpoint.failed:
                checkpoint.save()
                report()
                continue
            if should_stop and should_stop():
                logger.info('Обход остановлен, прогресс сохранён в %s', checkpoint.path)
                return False  # the rubric may be incomplete, it will be repeated on resume
            checkpoint.done.add((task.city, task.rubric))
            checkpoint.save()
            done_count += 1
            report()
        if checkpoint.failed:
            codes = ', '.join(sorted({r for _, r in checkpoint.failed}))
            logger.warning('Обход завершён, но не удалось обработать рубрик: %d (%s). '
                           'Повторите запуск с возобновлением, чтобы попробовать их снова.',
                           len(checkpoint.failed), codes)
        return True
    finally:
        writer.crawl_rubric = None
        checkpoint.save()
        if dedup.skipped:
            logger.info('Пропущено дублей: %d', dedup.skipped)
