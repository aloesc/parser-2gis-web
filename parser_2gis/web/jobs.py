from __future__ import annotations

import csv
import datetime
import os
import threading
import uuid
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from ..crawler import descendant_leaf_rubrics, leaf_rubrics, load_cities, load_rubrics
from ..logger import logger
from .events import EventHub
from .urls import build_search_urls

if TYPE_CHECKING:
    from ..config import Configuration

FORMATS = ('csv', 'xlsx', 'json')
MODES = ('urls', 'crawl')
BUSY_STATES = ('running', 'stopping')

CAPTCHA_MESSAGE = ('2GIS показал капчу (captcha.2gis.ru). Пройдите капчу в браузере или смените IP '
                   'и запустите обход повторно: прогресс сохранён.')


class JobError(Exception):
    """Base class of job errors; `status` is the HTTP status code."""
    status = 400

    def __init__(self, message: str, errors: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or {}


class JobValidationError(JobError):
    status = 422


class JobConflictError(JobError):
    status = 409


class JobSpec:
    """Validated request for a job."""
    def __init__(self, mode: str, output_path: str, format: str, urls: List[str],
                 cities: List[str], rubric_codes: Optional[List[str]], resume: bool) -> None:
        self.mode = mode
        self.output_path = output_path
        self.format = format
        self.urls = urls
        self.cities = cities
        self.rubric_codes = rubric_codes
        self.resume = resume


def _str_list(body: Dict[str, Any], key: str, errors: Dict[str, str]) -> List[str]:
    value = body.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        errors[key] = 'Ожидается список строк'
        return []
    return [x.strip() for x in value if x.strip()]


def validate_job_request(body: Dict[str, Any]) -> JobSpec:
    """Validate `POST /api/jobs` body.

    Raises:
        JobValidationError: With per-field messages.
    """
    errors: Dict[str, str] = {}

    mode = body.get('mode')
    if mode not in MODES:
        errors['mode'] = 'Допустимые значения: urls, crawl'
    fmt = body.get('format')
    if fmt not in FORMATS:
        errors['format'] = 'Допустимые значения: csv, xlsx, json'

    output_path = body.get('output_path')
    if not isinstance(output_path, str) or not output_path.strip():
        errors['output_path'] = 'Укажите путь к результирующему файлу'
        output_path = ''
    else:
        output_path = os.path.abspath(os.path.expanduser(output_path.strip()))
        if os.path.isdir(output_path):
            errors['output_path'] = 'Указан каталог, а не файл'
        elif not os.path.isdir(os.path.dirname(output_path)):
            errors['output_path'] = 'Каталог для результата не существует'

    resume = body.get('resume', True)
    if not isinstance(resume, bool):
        errors['resume'] = 'Ожидается true или false'
        resume = True

    urls = _str_list(body, 'urls', errors)
    cities = _str_list(body, 'cities', errors)
    query = body.get('query') or ''
    if not isinstance(query, str):
        errors['query'] = 'Ожидается строка'
        query = ''
    rubric_code = body.get('rubric_code')
    if rubric_code is not None and not isinstance(rubric_code, str):
        errors['rubric_code'] = 'Ожидается строка или null'
        rubric_code = None

    all_cities = load_cities()
    unknown = [c for c in cities if c not in all_cities]
    if unknown:
        errors['cities'] = 'Неизвестные коды городов: %s' % ', '.join(unknown)

    rubric_codes: Optional[List[str]] = None
    if rubric_code:
        rubrics = load_rubrics()
        rubric_codes = descendant_leaf_rubrics(rubrics, rubric_code)
        if not rubric_codes:
            errors['rubric_code'] = 'Рубрика не найдена или в ней нет листовых рубрик'

    if mode == 'urls' and not errors.keys() & {'urls', 'cities', 'query', 'rubric_code'}:
        if not urls:
            if query.strip() and cities:
                urls = build_search_urls(query.strip(), cities, rubric_code or None, all_cities)
            else:
                errors['urls'] = 'Укажите ссылки либо запрос и города'
        bad = [u for u in urls if not u.lower().startswith(('http://', 'https://'))]
        if bad:
            errors['urls'] = 'Ссылки должны начинаться с http:// или https://: %s' % bad[0]
    elif mode == 'crawl' and not cities and 'cities' not in errors:
        errors['cities'] = 'Выберите хотя бы один город'

    if errors:
        raise JobValidationError('Ошибка в параметрах задачи: ' + '; '.join(errors.values()), errors)

    if mode == 'crawl' and rubric_codes is None:
        rubric_codes = leaf_rubrics(load_rubrics())
    return JobSpec(str(mode), output_path, str(fmt), urls, cities, rubric_codes, resume)


def default_runner_factory(spec: JobSpec, config: Configuration,
                           on_progress: Callable[[int, int, int], None]) -> Any:
    """Create `GUIRunner` (thread runner). Imported lazily: it pulls Chrome and parser modules."""
    from ..runner.gui import GUIRunner

    if spec.mode == 'crawl':
        return GUIRunner([], spec.output_path, spec.format, config, crawl_cities=spec.cities,
                         crawl_rubrics=spec.rubric_codes, resume=spec.resume, on_progress=on_progress)
    return GUIRunner(spec.urls, spec.output_path, spec.format, config, on_progress=on_progress)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


class JobManager:
    """Holds the single current job. A runner is any object with `start()`, `stop()`, `join()`
    and optional `captcha` / `error_message` attributes (see `GUIRunner`)."""
    def __init__(self, hub: EventHub, runner_factory: Optional[Callable[..., Any]] = None) -> None:
        self._hub = hub
        self._runner_factory = runner_factory or default_runner_factory
        self._lock = threading.RLock()
        self._runner: Any = None
        self._spec: Optional[JobSpec] = None
        self._encoding = 'utf-8-sig'
        self._state: Dict[str, Any] = self._idle_state()
        self._stop_requested = False

    @staticmethod
    def _idle_state() -> Dict[str, Any]:
        return {'id': None, 'state': 'idle', 'mode': None, 'output_path': None, 'format': None,
                'started_at': None, 'finished_at': None,
                'progress': {'done': 0, 'total': 0, 'records': 0}, 'message': ''}

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            result = dict(self._state)
            result['progress'] = dict(self._state['progress'])
            result['stats_available'] = bool(self._spec and self._spec.mode == 'crawl'
                                             and os.path.isfile(self.stats_path() or ''))
            return result

    def stats_path(self) -> Optional[str]:
        with self._lock:
            if self._spec is None or self._spec.mode != 'crawl':
                return None
            return self._spec.output_path + '.stats.csv'

    def is_busy(self) -> bool:
        with self._lock:
            return self._state['state'] in BUSY_STATES

    def _publish(self) -> None:
        self._hub.publish_status(self.snapshot())

    def start(self, spec: JobSpec, config: Configuration) -> str:
        """Start a job.

        Raises:
            JobConflictError: Another job is running.
        """
        with self._lock:
            if self.is_busy():
                raise JobConflictError('Задача уже выполняется. Дождитесь завершения или остановите её.')

            job_id = uuid.uuid4().hex[:12]
            self._spec = spec
            self._encoding = config.writer.encoding
            self._stop_requested = False
            self._state = {'id': job_id, 'state': 'running', 'mode': spec.mode, 'output_path': spec.output_path,
                           'format': spec.format, 'started_at': _now(), 'finished_at': None,
                           'progress': {'done': 0, 'total': 0, 'records': 0}, 'message': ''}

            def on_progress(done: int, total: int, records: int) -> None:
                with self._lock:
                    if self._state['id'] == job_id:
                        self._state['progress'] = {'done': done, 'total': total, 'records': records}
                self._publish()

            try:
                runner = self._runner_factory(spec, config.copy(deep=True), on_progress)
                self._runner = runner
                runner.start()
            except Exception:
                self._runner = None
                self._state.update(state='error', finished_at=_now(), message='Не удалось запустить задачу.')
                logger.error('Не удалось запустить задачу.', exc_info=True)
                self._publish()
                raise

            threading.Thread(target=self._watch, args=(job_id, runner), daemon=True).start()
        self._publish()
        return job_id

    def stop(self) -> None:
        """Ask the runner to stop.

        Raises:
            JobConflictError: There is no running job.
        """
        with self._lock:
            if self._state['state'] not in BUSY_STATES:
                raise JobConflictError('Нет выполняющейся задачи.')
            runner = self._runner
            if self._state['state'] == 'stopping':
                return
            self._stop_requested = True
            self._state.update(state='stopping', message='Остановка…')
        self._publish()
        # Closing the browser may take a while, don't block the HTTP request.
        threading.Thread(target=self._stop_runner, args=(runner,), daemon=True).start()

    @staticmethod
    def _stop_runner(runner: Any) -> None:
        try:
            runner.stop()
        except Exception:
            logger.error('Ошибка при остановке задачи.', exc_info=True)

    def _watch(self, job_id: str, runner: Any) -> None:
        runner.join()
        with self._lock:
            if self._state['id'] != job_id:
                return
            error_message = getattr(runner, 'error_message', None)
            if getattr(runner, 'captcha', False):
                state, message = 'captcha', CAPTCHA_MESSAGE
            elif error_message:
                state, message = 'error', error_message
            elif self._stop_requested:
                state, message = 'done', 'Остановлено пользователем.'
            else:
                state, message = 'done', 'Готово.'
                failed = getattr(runner, 'failed_rubrics', None)
                if failed:
                    message = ('Готово, но не удалось обработать рубрик: %d (%s). '
                               'Запустите снова с «Продолжить с места остановки».' % (len(failed), ', '.join(failed)))
            self._state.update(state=state, message=message, finished_at=_now())
            self._runner = None
        self._publish()

    def wait(self, timeout: float = 5.0) -> bool:
        """Wait until the current job is finished (for tests and shutdown)."""
        end = datetime.datetime.now() + datetime.timedelta(seconds=timeout)
        while datetime.datetime.now() < end:
            if not self.is_busy():
                return True
            threading.Event().wait(0.02)
        return not self.is_busy()

    def read_stats(self) -> Optional[Dict[str, Any]]:
        """Rows of `<output>.stats.csv` of the current/last crawl job, `None` if there is no such file."""
        path = self.stats_path()
        if not path or not os.path.isfile(path):
            return None
        for encoding in (self._encoding, 'utf-8-sig', 'cp1251'):
            try:
                with open(path, encoding=encoding, newline='') as f:
                    rows = [_stat_row(r) for r in csv.reader(f)][1:]
                return {'rows': [r for r in rows if r], 'path': path}
            except (UnicodeError, LookupError):
                continue
        return None


def _stat_row(cells: List[str]) -> Optional[Dict[str, Any]]:
    if len(cells) < 5:
        return None
    try:
        return {'rubric': cells[0], 'group': cells[1], 'total': int(cells[2]),
                'without_site': int(cells[3]), 'share': float(cells[4].replace(',', '.'))}
    except ValueError:
        return None
