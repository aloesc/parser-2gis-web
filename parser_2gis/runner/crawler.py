from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

from ..chrome.exceptions import ChromeCaptchaException
from ..crawler import Checkpoint, build_tasks, crawl, leaf_rubrics, load_rubrics, read_rubrics_file
from ..crawler.crawler import checkpoint_path
from ..exceptions import ChromeRuntimeException, ChromeUserAbortException
from ..logger import logger
from ..parser import get_parser
from ..writer import get_writer
from ..writer.stats import RubricStat, stats_from_csv, write_stats
from .runner import AbstractRunner

if TYPE_CHECKING:
    from ..config import Configuration


def prepare_resume(output_path: str, checkpoint: Checkpoint, resume: bool) -> None:
    """Load checkpoint or reset it. A writer truncates its file on open, so the output
    of an interrupted run is moved to `<output>.partN` to keep collected data."""
    if not resume:
        checkpoint.remove()
    elif checkpoint.load():
        logger.info('Продолжение обхода: рубрик пройдено %d, записей учтено %d.',
                    len(checkpoint.done), len(checkpoint.ids))
    if checkpoint.done and os.path.exists(output_path):
        n = 1
        while os.path.exists(f'{output_path}.part{n}'):
            n += 1
        os.replace(output_path, f'{output_path}.part{n}')
        logger.warning('Предыдущий результат сохранён в %s.part%d', output_path, n)


def merge_stats(parts: list[list[RubricStat]]) -> list[RubricStat]:
    """Sum statistics of several result files (an interrupted and resumed run produces `.partN` files)."""
    totals: dict[tuple[str, str], list[int]] = {}
    for stats in parts:
        for st in stats:
            acc = totals.setdefault((st.rubric, st.group), [0, 0])
            acc[0] += st.total
            acc[1] += st.without_site
    merged = [RubricStat(rubric=r, group=g, total=t, without_site=w, share_without_site=round(w / t, 4) if t else 0.0)
              for (r, g), (t, w) in totals.items()]
    merged.sort(key=lambda x: (-x.share_without_site, -x.total, x.rubric))
    return merged


def write_run_stats(output_path: str, format: str, join_char: str, encoding: str) -> None:
    """Write `<output>.stats.csv` for CSV results. XLSX gets a stats sheet from its writer."""
    if format == 'xlsx':
        return
    if format != 'csv':
        logger.warning('Статистика по сайтам доступна только для форматов csv и xlsx.')
        return

    files = [output_path] + [f'{output_path}.part{n}' for n in range(1, 1000) if os.path.exists(f'{output_path}.part{n}')]
    files = [f for f in files if os.path.exists(f)]
    stats = merge_stats([stats_from_csv(f, join_char, encoding) for f in files])
    stats_path = f'{output_path}.stats.csv'
    write_stats(stats, stats_path, encoding)
    logger.info('Статистика по рубрикам сохранена в %s (рубрик: %d).', stats_path, len(stats))


class CrawlerRunner(AbstractRunner):
    """CLI runner that walks through cities x leaf rubrics.

    Args:
        cities: City codes.
        rubrics_file: File with rubric codes, all leaf rubrics if `None`.
        resume: Continue from checkpoint.
    """
    def __init__(self, cities: list[str], output_path: str, format: str, config: Configuration,
                 rubrics_file: Optional[str] = None, resume: bool = True) -> None:
        super().__init__([], output_path, format, config)
        self._cities = cities
        self._rubrics_file = rubrics_file
        self._resume = resume

    def start(self):
        logger.info('Парсинг запущен.')
        checkpoint_file = checkpoint_path(self._output_path)
        try:
            if self._rubrics_file:
                codes = read_rubrics_file(self._rubrics_file)
            else:
                codes = leaf_rubrics(load_rubrics())
            tasks = build_tasks(self._cities, codes)
            checkpoint = Checkpoint(checkpoint_file)
            prepare_resume(self._output_path, checkpoint, self._resume)

            # Site flag and rubrics are required for statistics
            csv_options = self._config.writer.csv
            csv_options.add_rubrics = True
            csv_options.add_site_flag = True
            csv_options.add_rubric_codes = True

            def factory(url: str):
                return get_parser(url, chrome_options=self._config.chrome, parser_options=self._config.parser)

            with get_writer(self._output_path, self._format, self._config.writer) as writer:
                crawl(tasks, writer, factory, checkpoint)
            if checkpoint.failed:
                logger.warning('Не удалось обработать рубрик: %d.', len(checkpoint.failed))
            write_run_stats(self._output_path, self._format, csv_options.join_char, self._config.writer.encoding)
        except ChromeCaptchaException:
            logger.error('2GIS показал капчу (captcha.2gis.ru). Обход остановлен, прогресс сохранён в %s. '
                         'Пройдите капчу в браузере/смените IP и запустите команду повторно.',
                         checkpoint_file)
        except (KeyboardInterrupt, ChromeUserAbortException):
            logger.error('Работа парсера прервана пользователем.')
        except ValueError as e:
            logger.error('%s', e)
        except Exception as e:
            if isinstance(e, ChromeRuntimeException) and str(e) == 'Tab has been stopped':
                logger.error('Вкладка браузера была закрыта.')
            else:
                logger.error('Ошибка во время работы парсера.', exc_info=True)
        finally:
            logger.info('Парсинг завершён.')

    def stop(self):
        pass
