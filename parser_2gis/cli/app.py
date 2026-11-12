from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..logger import setup_cli_logger
from ..runner import CLIRunner, CrawlerRunner

if TYPE_CHECKING:
    from ..config import Configuration


def cli_app(urls: list[str], output_path: str, format: str, config: Configuration,
            cities: Optional[list[str]] = None, rubrics_file: Optional[str] = None,
            resume: bool = True) -> None:
    setup_cli_logger(config.log)

    if cities:
        CrawlerRunner(cities, output_path, format, config, rubrics_file, resume).start()
        return

    runner = CLIRunner(urls, output_path, format, config)
    runner.start()
