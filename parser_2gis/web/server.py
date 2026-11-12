from __future__ import annotations

import hmac
import http.server
import json
import mimetypes
import os
import pathlib
import queue
import secrets
import socket
import threading
import urllib.parse
from typing import Any, Dict, Optional, Tuple

from ..crawler import load_cities, load_rubrics
from ..logger import logger
from ..version import version
from .events import EventHub, attach_log_handler, detach_log_handler
from .jobs import JobError, JobManager, validate_job_request
from .settings import ConfigStore, ConfigValidationError

DEFAULT_PORT = 8765
MAX_BODY_SIZE = 1024 * 1024
SSE_KEEPALIVE_SECONDS = 15.0
TOKEN_PLACEHOLDER = '{{TOKEN}}'
STATIC_DIR = pathlib.Path(__file__).resolve().parent / 'static'

COUNTRY_NAMES = dict(
    ae='Объединенные Арабские Эмираты', iq='Ирак',
    az='Азербайджан', bh='Бахрейн', by='Беларусь', cl='Чили', cy='Кипр', cz='Чехия',
    eg='Египет', it='Италия', kg='Киргизия', kw='Кувейт', kz='Казахстан', om='Оман',
    qa='Катар', ru='Россия', sa='Саудовская Аравия', uz='Узбекистан')

CONTENT_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
    '.woff2': 'font/woff2',
    '.txt': 'text/plain; charset=utf-8',
}


def default_output_path() -> str:
    base = os.path.expanduser('~')
    documents = os.path.join(base, 'Documents')
    if os.path.isdir(documents):
        base = documents
    return os.path.join(base, '2gis_result.csv')


class WebServer(http.server.ThreadingHTTPServer):
    """Local web UI server bound to 127.0.0.1 only."""
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, port: int = DEFAULT_PORT, static_dir: Optional[pathlib.Path] = None,
                 config_path: Optional[pathlib.Path] = None, runner_factory: Any = None,
                 token: Optional[str] = None, find_free_port: bool = True,
                 keepalive: float = SSE_KEEPALIVE_SECONDS) -> None:
        self.static_dir = pathlib.Path(static_dir or STATIC_DIR).resolve()
        self.token = token or secrets.token_urlsafe(24)
        self.keepalive = keepalive
        self.hub = EventHub()
        self.jobs = JobManager(self.hub, runner_factory)
        self.settings = ConfigStore(config_path)
        self._closing = threading.Event()

        last_error: Optional[OSError] = None
        candidates = range(port, port + 100) if find_free_port and port else [port]
        for candidate in candidates:
            try:
                super().__init__(('127.0.0.1', candidate), RequestHandler)
                break
            except OSError as e:
                last_error = e
        else:
            raise OSError(f'Не удалось занять порт, начиная с {port}: {last_error}')

        self._closing.clear()  # a failed bind attempt calls server_close() too
        self.port = self.server_address[1]
        self._log_handler: Optional[Tuple[Any, int]] = attach_log_handler(self.hub)

    @property
    def closing(self) -> bool:
        return self._closing.is_set()

    @property
    def url(self) -> str:
        return f'http://127.0.0.1:{self.port}/'

    def allowed_hosts(self) -> Tuple[str, ...]:
        return (f'127.0.0.1:{self.port}', f'localhost:{self.port}')

    def allowed_origins(self) -> Tuple[str, ...]:
        return tuple(f'http://{h}' for h in self.allowed_hosts())

    def server_close(self) -> None:
        self._closing.set()
        super().server_close()
        handler, self._log_handler = getattr(self, '_log_handler', None), None
        if handler:  # TCPServer calls server_close() itself when binding fails
            detach_log_handler(*handler)


class RequestHandler(http.server.BaseHTTPRequestHandler):
    server: WebServer  # type: ignore[assignment]
    protocol_version = 'HTTP/1.0'  # one request per connection: no keep-alive desync on unread bodies
    server_version = 'Parser2GISWeb'

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Don't spam the log with access lines

    # ---- helpers ----

    def _send_json(self, status: int, data: Any) -> None:
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, **extra: Any) -> None:
        self._send_json(status, {'error': message, **extra})

    def _check_origin(self) -> bool:
        """Host and Origin must point to our own address (protects against DNS rebinding and CSRF)."""
        host = self.headers.get('Host', '')
        if host.lower() not in self.server.allowed_hosts():
            self._error(403, 'Недопустимый заголовок Host.')
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin.lower() not in self.server.allowed_origins():
            self._error(403, 'Недопустимый заголовок Origin.')
            return False
        return True

    def _check_token(self) -> bool:
        token = self.headers.get('X-Token', '')
        if not hmac.compare_digest(token.encode('utf-8'), self.server.token.encode('utf-8')):
            self._error(403, 'Неверный или отсутствующий токен X-Token.')
            return False
        return True

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = -1
        if length < 0 or length > MAX_BODY_SIZE:
            self._error(400, 'Некорректная длина тела запроса.')
            return None
        raw = self.rfile.read(length) if length else b''
        try:
            data = json.loads(raw.decode('utf-8')) if raw else {}
        except (ValueError, UnicodeDecodeError):
            self._error(400, 'Тело запроса не является корректным JSON.')
            return None
        if not isinstance(data, dict):
            self._error(400, 'Тело запроса должно быть JSON-объектом.')
            return None
        return data

    def _route(self, method: str) -> None:
        try:
            if not self._check_origin():
                return
            if method in ('POST', 'PUT') and not self._check_token():
                return

            path = urllib.parse.urlsplit(self.path).path
            if method == 'GET':
                self._do_get(path)
            elif method == 'POST':
                self._do_post(path)
            else:
                self._do_put(path)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            logger.error('Ошибка веб-сервера.', exc_info=True)
            try:
                self._error(500, 'Внутренняя ошибка сервера.')
            except Exception:
                pass

    def do_GET(self) -> None:
        self._route('GET')

    def do_POST(self) -> None:
        self._route('POST')

    def do_PUT(self) -> None:
        self._route('PUT')

    def _not_found(self) -> None:
        self._error(404, 'Не найдено.')

    # ---- GET ----

    def _do_get(self, path: str) -> None:
        if path in ('/', '/index.html'):
            self._send_index()
        elif path.startswith('/static/'):
            self._send_static(path[len('/static/'):])
        elif path == '/api/bootstrap':
            self._bootstrap()
        elif path == '/api/rubrics':
            self._send_json(200, load_rubrics())
        elif path == '/api/jobs/current':
            self._send_json(200, self.server.jobs.snapshot())
        elif path == '/api/events':
            self._events()
        elif path == '/api/stats':
            stats = self.server.jobs.read_stats()
            if stats is None:
                self._error(404, 'Статистика недоступна: нет результатов обхода.')
            else:
                self._send_json(200, stats)
        else:
            self._not_found()

    def _send_bytes(self, status: int, body: bytes, content_type: str, cache: str) -> None:
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def _send_index(self) -> None:
        index = self.server.static_dir / 'index.html'
        try:
            html = index.read_text(encoding='utf-8')
        except OSError:
            self._error(404, 'index.html не найден.')
            return
        html = html.replace(TOKEN_PLACEHOLDER, self.server.token)
        self._send_bytes(200, html.encode('utf-8'), 'text/html; charset=utf-8', 'no-store')

    def _send_static(self, relative: str) -> None:
        relative = urllib.parse.unquote(relative)
        if not relative or '\0' in relative or '\\' in relative or relative.startswith('/'):
            self._not_found()
            return
        root = str(self.server.static_dir)
        target = os.path.realpath(os.path.join(root, relative))
        try:
            inside = os.path.commonpath([root, target]) == root
        except ValueError:  # another drive on Windows
            inside = False
        if not inside or not os.path.isfile(target):
            self._not_found()
            return
        ext = os.path.splitext(target)[1].lower()
        content_type = CONTENT_TYPES.get(ext) or mimetypes.guess_type(target)[0] or 'application/octet-stream'
        try:
            with open(target, 'rb') as f:
                body = f.read()
        except OSError:
            self._not_found()
            return
        if ext == '.html':
            body = body.replace(TOKEN_PLACEHOLDER.encode(), self.server.token.encode())
        self._send_bytes(200, body, content_type, 'no-cache')

    def _bootstrap(self) -> None:
        cities = load_cities()
        used = {c['country_code'] for c in cities.values()}
        countries = [{'code': code, 'name': name} for code, name in COUNTRY_NAMES.items() if code in used]
        countries.sort(key=lambda x: x['name'])
        self._send_json(200, {
            'version': version,
            'default_output_path': default_output_path(),
            'countries': countries,
            'cities': [{'name': c['name'], 'code': c['code'], 'domain': c['domain'],
                        'country_code': c['country_code']} for c in cities.values()],
            'config': self.server.settings.public(),
        })

    def _events(self) -> None:
        server = self.server
        q, backlog = server.hub.subscribe()
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            self.close_connection = True

            self._sse('status', server.jobs.snapshot())
            for entry in backlog:
                self._sse('log', entry)
            while not server.closing:
                try:
                    kind, payload = q.get(timeout=server.keepalive)
                except queue.Empty:
                    self.wfile.write(b': keep-alive\n\n')
                    self.wfile.flush()
                    continue
                self._sse(kind, payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, socket.timeout):
            pass
        finally:
            server.hub.unsubscribe(q)

    def _sse(self, event: str, data: Any) -> None:
        payload = json.dumps(data, ensure_ascii=False)
        self.wfile.write(f'event: {event}\ndata: {payload}\n\n'.encode('utf-8'))
        self.wfile.flush()

    # ---- POST / PUT ----

    def _do_post(self, path: str) -> None:
        if path == '/api/jobs':
            body = self._read_json()
            if body is None:
                return
            jobs = self.server.jobs
            if jobs.is_busy():
                self._error(409, 'Задача уже выполняется. Дождитесь завершения или остановите её.')
                return
            try:
                spec = validate_job_request(body)
                job_id = jobs.start(spec, self.server.settings.get())
            except JobError as e:
                self._error(e.status, e.message, errors=e.errors)
                return
            except Exception:
                self._error(500, 'Не удалось запустить задачу.')
                return
            self._send_json(201, {'id': job_id})
        elif path == '/api/jobs/current/stop':
            self._read_json()  # drain body, if any
            try:
                self.server.jobs.stop()
            except JobError as e:
                self._error(e.status, e.message)
                return
            self._send_json(202, {'state': 'stopping'})
        else:
            self._not_found()

    def _do_put(self, path: str) -> None:
        if path != '/api/config':
            self._not_found()
            return
        body = self._read_json()
        if body is None:
            return
        try:
            config = self.server.settings.update(body)
        except ConfigValidationError as e:
            self._error(422, 'Некорректные настройки: ' + str(e), errors=e.errors)
            return
        self._send_json(200, {'config': config})
