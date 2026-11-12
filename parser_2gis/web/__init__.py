from __future__ import annotations

import threading
import webbrowser
from typing import Optional

from ..logger import logger

__all__ = ['run_web']


def run_web(port: int = 8765, open_browser: bool = True) -> None:
    """Run the local web UI until interrupted. Doesn't import the desktop GUI."""
    from ..config import Configuration
    from ..logger import setup_cli_logger
    from .server import WebServer

    # Console logger first: the server adds its own handler to the same logger
    setup_cli_logger(Configuration.load_config(auto_create=False).log)
    server = WebServer(port=port)
    url = server.url
    if server.port != port:
        logger.warning('Порт %d занят, используется %d.', port, server.port)
    logger.info('Веб-интерфейс запущен: %s (остановка: Ctrl+C)', url)

    if open_browser:
        timer: Optional[threading.Timer] = threading.Timer(0.5, webbrowser.open, args=(url,))
        timer.daemon = True  # type: ignore[union-attr]
        timer.start()  # type: ignore[union-attr]

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('Остановка веб-интерфейса.')
    finally:
        if server.jobs.is_busy():
            try:
                server.jobs.stop()
                server.jobs.wait(10)
            except Exception:
                pass
        server.server_close()
