from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..chrome.exceptions import ChromeCaptchaException
from ..crawler import Checkpoint, build_tasks, crawl, leaf_rubrics, load_rubrics
from ..crawler.crawler import checkpoint_path
from ..exceptions import ChromeRuntimeException, ChromeUserAbortException
from ..logger import logger
from ..parser import get_parser
from ..writer import get_writer
from .crawler import prepare_resume, write_run_stats
from .runner import AbstractRunner

if TYPE_CHECKING:
    from ..config import Configuration


class GUIRunner(AbstractRunner, threading.Thread):
    """GUI thread runner.

    Args:
        urls: 2GIS URLs with items to be collected.
        output_path: Path to the result file.
        format: `csv`, `xlsx` or `json` format.
        config: Configuration.
        crawl_cities: City codes. If set, the runner works in the "whole city" mode:
            walks through cities x leaf rubrics instead of `urls`.
        crawl_rubrics: Rubric codes for the crawl mode, all leaf rubrics if `None`.
        resume: Continue the crawl from checkpoint.
        on_progress: Optional `(done, total, records)` callback.

    After the thread finishes `captcha` and `error_message` tell how it ended.
    """
    def __init__(self, urls: list[str], output_path: str, format: str,
                 config: Configuration, crawl_cities: Optional[list[str]] = None,
                 crawl_rubrics: Optional[list[str]] = None, resume: bool = True,
                 on_progress: Optional[Callable[[int, int, int], None]] = None) -> None:
        AbstractRunner.__init__(self, urls, output_path, format, config)
        threading.Thread.__init__(self)

        self._crawl_cities = crawl_cities
        self._crawl_rubrics = crawl_rubrics
        self._resume = resume
        self._on_progress = on_progress
        self.captcha = False
        self.error_message: Optional[str] = None
        self.failed_rubrics: list[str] = []  # crawl mode: rubric codes that could not be processed

        self._parser = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start thread."""
        self._cancelled = False
        logger.info('Парсинг запущен.')
        threading.Thread.start(self)

    def stop(self) -> None:
        """Stop thread."""
        if not self._started.is_set():  # type: ignore
            raise RuntimeError('start() is not called')

        if self._cancelled:
            return  # We can stop the thread only once

        self._cancelled = True
        self._stop_parser()

    def _stop_parser(self) -> None:
        """Close parser if it's been opened."""
        with self._lock:
            if self._parser:
                self._parser.close()
                self._parser = None

    def _run_crawl(self) -> None:
        """Whole city mode: cities x leaf rubrics with checkpoint and dedup."""
        assert self._crawl_cities
        checkpoint_file = checkpoint_path(self._output_path)
        try:
            codes = self._crawl_rubrics if self._crawl_rubrics is not None else leaf_rubrics(load_rubrics())
            tasks = build_tasks(self._crawl_cities, codes)
            checkpoint = Checkpoint(checkpoint_file)
            prepare_resume(self._output_path, checkpoint, self._resume)

            # Don't touch user's config: site flag and rubrics are required for statistics
            config = self._config.copy(deep=True)
            csv_options = config.writer.csv
            csv_options.add_rubrics = True
            csv_options.add_site_flag = True
            csv_options.add_rubric_codes = True

            def factory(url: str):
                return _ParserHolder(self, url, config)

            with get_writer(self._output_path, self._format, config.writer) as writer:
                completed = crawl(tasks, writer, factory, checkpoint,
                                  should_stop=lambda: self._cancelled, on_progress=self._on_progress)
            self.failed_rubrics = sorted({r for _, r in checkpoint.failed})
            if completed:
                write_run_stats(self._output_path, self._format, csv_options.join_char, config.writer.encoding)
        except ChromeCaptchaException:
            if not self._cancelled:
                self.captcha = True
                logger.error('2GIS показал капчу (captcha.2gis.ru). Обход остановлен, прогресс сохранён в %s. '
                             'Пройдите капчу в браузере/смените IP и запустите парсинг повторно.',
                             checkpoint_file)
        except ValueError as e:
            self.error_message = str(e)
            logger.error('%s', e)
        except Exception as e:
            if not self._cancelled:  # Don't catch intended exceptions caused by stopping parser
                if isinstance(e, ChromeRuntimeException) and str(e) == 'Tab has been stopped':
                    self.error_message = 'Вкладка браузера была закрыта.'
                    logger.error('Вкладка браузера была закрыта.')
                elif isinstance(e, ChromeUserAbortException):
                    self.error_message = 'Работа парсера прервана пользователем.'
                    logger.error('Работа парсера прервана пользователем.')
                else:
                    self.error_message = 'Ошибка во время работы парсера.'
                    logger.error('Ошибка во время работы парсера.', exc_info=True)
        finally:
            self._stop_parser()
            logger.info('Парсинг завершён.')

    def run(self) -> None:
        """Thread's activity."""
        if self._crawl_cities:
            self._run_crawl()
            return

        with get_writer(self._output_path, self._format, self._config.writer) as raw_writer:
            writer: Any = _CountingWriter(raw_writer) if self._on_progress else raw_writer
            total = len(self._urls)
            if self._on_progress:
                self._on_progress(0, total, 0)
            for index, url in enumerate(self._urls, 1):
                try:
                    logger.info(f'Парсинг ссылки {url}')
                    self._parser = get_parser(url,
                                              chrome_options=self._config.chrome,
                                              parser_options=self._config.parser)
                    assert self._parser

                    if not self._cancelled:
                        self._parser.parse(writer)
                except ChromeCaptchaException:
                    if not self._cancelled:
                        self.captcha = True
                        logger.error('2GIS показал капчу (captcha.2gis.ru). Пройдите капчу в браузере/смените IP.')
                        break
                except Exception as e:
                    if not self._cancelled:  # Don't catch intended exceptions caused by stopping parser
                        if isinstance(e, ChromeRuntimeException) and str(e) == 'Tab has been stopped':
                            self.error_message = self.error_message or 'Вкладка браузера была закрыта.'
                            logger.error('Вкладка браузера была закрыта.')
                        elif isinstance(e, ChromeUserAbortException):
                            self.error_message = self.error_message or 'Работа парсера прервана пользователем.'
                            logger.error('Работа парсера прервана пользователем.')
                        else:
                            self.error_message = self.error_message or 'Ошибка во время работы парсера.'
                            logger.error('Ошибка во время работы парсера.', exc_info=True)
                finally:
                    logger.info('Парсинг ссылки завершён.')
                    self._stop_parser()
                    if self._on_progress and not self._cancelled:
                        self._on_progress(index, total, writer.records)
                    if self._cancelled:
                        break

        logger.info('Парсинг завершён.')


class _ParserHolder:
    """Context manager that registers the opened parser in the runner, so `stop()` can close it."""
    def __init__(self, runner: GUIRunner, url: str, config: Configuration) -> None:
        self._runner = runner
        self._url = url
        self._config = config

    def __enter__(self):
        parser = get_parser(self._url, chrome_options=self._config.chrome, parser_options=self._config.parser)
        with self._runner._lock:
            self._runner._parser = parser
        if self._runner._cancelled:
            self._runner._stop_parser()
        return parser

    def __exit__(self, *exc) -> None:
        self._runner._stop_parser()


class _CountingWriter:
    """Writer proxy that counts written records (for progress reporting)."""
    def __init__(self, writer: Any) -> None:
        self._writer = writer
        self.records = 0

    def write(self, catalog_doc: Any) -> None:
        self._writer.write(catalog_doc)
        self.records += 1

    def __getattr__(self, name: str) -> Any:
        return getattr(self._writer, name)
