from __future__ import annotations

import http.client
import json
import threading
import time

import pytest

from parser_2gis.config import Configuration
from parser_2gis.web.server import WebServer

TOKEN = 'test-token'


class FakeRunner:
    """Runner stub: runs until `release` is set or `stop()` is called."""
    instances: list = []

    def __init__(self, spec, config, on_progress, behaviour='done'):
        self.spec, self.config, self.on_progress, self.behaviour = spec, config, on_progress, behaviour
        self.captcha = False
        self.error_message = None
        self.release = threading.Event()
        self.stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        FakeRunner.instances.append(self)

    def _run(self):
        self.on_progress(1, 4, 7)
        self.release.wait(5)
        if self.behaviour == 'captcha':
            self.captcha = True
        elif self.behaviour == 'error':
            self.error_message = 'Ошибка во время работы парсера.'

    def start(self):
        self._thread.start()

    def stop(self):
        self.stopped = True
        self.release.set()

    def join(self):
        self._thread.join()


@pytest.fixture
def server(tmp_path):
    static = tmp_path / 'static'
    static.mkdir()
    (static / 'index.html').write_text('<meta name="token" content="{{TOKEN}}">', encoding='utf-8')
    (static / 'app.js').write_text('console.log(1)', encoding='utf-8')
    (static / 'style.css').write_text('body{}', encoding='utf-8')
    (tmp_path / 'secret.txt').write_text('secret', encoding='utf-8')

    FakeRunner.instances = []
    srv = WebServer(port=18765, static_dir=static, config_path=tmp_path / 'p.config', token=TOKEN,
                    runner_factory=lambda spec, config, cb: FakeRunner(spec, config, cb), keepalive=0.2)
    srv.tmp = tmp_path  # type: ignore[attr-defined]
    thread = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True)
    thread.start()
    yield srv
    for r in FakeRunner.instances:
        r.release.set()
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None, token=True, host=None):
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=5)
    hdrs = {}
    if host is not None:
        hdrs['Host'] = host
    if token and method in ('POST', 'PUT'):
        hdrs['X-Token'] = TOKEN
    hdrs.update(headers or {})
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode('utf-8')
        hdrs['Content-Type'] = 'application/json'
    conn.request(method, path, body=data, headers=hdrs)
    resp = conn.getresponse()
    raw = resp.read()
    conn.close()
    return resp, raw


def jreq(srv, *args, **kwargs):
    resp, raw = request(srv, *args, **kwargs)
    return resp.status, json.loads(raw.decode('utf-8'))


def job_body(srv, **kw):
    body = {'mode': 'crawl', 'cities': ['moscow'], 'rubric_code': None, 'format': 'csv',
            'output_path': str(srv.tmp / 'out.csv'), 'resume': False}
    body.update(kw)
    return body


# ---- static / index ----

def test_index_token_substituted(server):
    resp, raw = request(server, 'GET', '/')
    assert resp.status == 200
    assert 'text/html' in resp.getheader('Content-Type')
    assert resp.getheader('Cache-Control') == 'no-store'
    assert f'content="{TOKEN}"' in raw.decode('utf-8')
    assert '{{TOKEN}}' not in raw.decode('utf-8')


def test_static_content_types(server):
    resp, raw = request(server, 'GET', '/static/app.js')
    assert resp.status == 200 and 'javascript' in resp.getheader('Content-Type')
    assert resp.getheader('Cache-Control')
    resp, _ = request(server, 'GET', '/static/style.css')
    assert 'text/css' in resp.getheader('Content-Type')
    resp, _ = request(server, 'GET', '/static/missing.js')
    assert resp.status == 404


@pytest.mark.parametrize('path', [
    '/static/../secret.txt', '/static/%2e%2e/secret.txt', '/static/..%2fsecret.txt',
    '/static/%2e%2e%2fsecret.txt', '/static//etc/passwd', '/static/..\\secret.txt', '/static/',
])
def test_path_traversal(server, path):
    resp, raw = request(server, 'GET', path)
    assert resp.status in (403, 404)
    assert b'secret' not in raw


# ---- security ----

def test_foreign_host_rejected(server):
    status, data = jreq(server, 'GET', '/api/bootstrap', host='evil.example.com')
    assert status == 403 and 'error' in data
    status, _ = jreq(server, 'GET', '/api/bootstrap', host=f'127.0.0.1:{server.port + 1}')
    assert status == 403
    status, _ = jreq(server, 'GET', '/api/bootstrap', host=f'localhost:{server.port}')
    assert status == 200


def test_foreign_origin_rejected(server):
    status, _ = jreq(server, 'GET', '/api/bootstrap', headers={'Origin': 'http://evil.example.com'})
    assert status == 403
    status, _ = jreq(server, 'POST', '/api/jobs', job_body(server), headers={'Origin': 'http://evil.example.com'})
    assert status == 403
    status, _ = jreq(server, 'GET', '/api/bootstrap', headers={'Origin': f'http://127.0.0.1:{server.port}'})
    assert status == 200


def test_token_required_for_post_and_put(server):
    assert jreq(server, 'POST', '/api/jobs', job_body(server), token=False)[0] == 403
    assert jreq(server, 'PUT', '/api/config', {}, token=False)[0] == 403
    assert jreq(server, 'POST', '/api/jobs/current/stop', {}, token=False)[0] == 403
    assert jreq(server, 'PUT', '/api/config', {}, headers={'X-Token': 'wrong'})[0] == 403
    assert not FakeRunner.instances


# ---- bootstrap / rubrics ----

def test_bootstrap_structure(server):
    status, data = jreq(server, 'GET', '/api/bootstrap')
    assert status == 200
    assert set(data) == {'version', 'default_output_path', 'countries', 'cities', 'config'}
    assert data['version'] and data['default_output_path']
    assert {'code': 'ru', 'name': 'Россия'} in data['countries']
    assert set(data['cities'][0]) == {'name', 'code', 'domain', 'country_code'}
    assert any(c['code'] == 'moscow' for c in data['cities'])
    assert set(data['config']) == {'chrome', 'parser', 'writer'}
    assert 'headless' in data['config']['chrome']


def test_rubrics(server):
    status, data = jreq(server, 'GET', '/api/rubrics')
    assert status == 200 and data
    item = next(iter(data.values()))
    assert {'label', 'code', 'parentCode', 'children'} <= set(item)


# ---- config ----

def test_put_config_saves(server):
    status, data = jreq(server, 'PUT', '/api/config', {'parser': {'max_records': 42}, 'chrome': {'headless': True}})
    assert status == 200
    assert data['config']['parser']['max_records'] == 42
    assert data['config']['chrome']['headless'] is True
    saved = Configuration.load_config(server.tmp / 'p.config', auto_create=False)
    assert saved.parser.max_records == 42
    assert jreq(server, 'GET', '/api/bootstrap')[1]['config']['parser']['max_records'] == 42


def test_put_config_validation_422(server):
    before = jreq(server, 'GET', '/api/bootstrap')[1]['config']
    status, data = jreq(server, 'PUT', '/api/config', {'parser': {'max_records': 'много'}})
    assert status == 422
    assert 'parser.max_records' in data['errors']
    assert data['error']
    status, data = jreq(server, 'PUT', '/api/config', {'log': {'level': 'DEBUG'}})
    assert status == 422 and 'log' in data['errors']
    assert jreq(server, 'GET', '/api/bootstrap')[1]['config'] == before


def test_bad_json_body(server):
    status, data = jreq(server, 'PUT', '/api/config', b'{not json')
    assert status == 400 and data['error']
    status, _ = jreq(server, 'PUT', '/api/config', b'[1]')
    assert status == 400


# ---- jobs ----

def test_idle_status(server):
    status, data = jreq(server, 'GET', '/api/jobs/current')
    assert status == 200
    assert data['id'] is None and data['state'] == 'idle'
    assert data['progress'] == {'done': 0, 'total': 0, 'records': 0}
    assert data['stats_available'] is False


def test_job_lifecycle_and_conflict(server):
    status, data = jreq(server, 'POST', '/api/jobs', job_body(server))
    assert status == 201 and data['id']
    job_id = data['id']

    status, cur = jreq(server, 'GET', '/api/jobs/current')
    assert cur['id'] == job_id and cur['state'] == 'running' and cur['mode'] == 'crawl'
    assert cur['format'] == 'csv' and cur['output_path'].endswith('out.csv') and cur['started_at']

    deadline = time.time() + 3
    while time.time() < deadline and cur['progress']['done'] != 1:
        time.sleep(0.02)
        cur = jreq(server, 'GET', '/api/jobs/current')[1]
    assert cur['progress'] == {'done': 1, 'total': 4, 'records': 7}

    status, data = jreq(server, 'POST', '/api/jobs', job_body(server))
    assert status == 409 and data['error']
    assert len(FakeRunner.instances) == 1

    FakeRunner.instances[0].release.set()
    assert server.jobs.wait(3)
    cur = jreq(server, 'GET', '/api/jobs/current')[1]
    assert cur['state'] == 'done' and cur['finished_at']

    # a new job is allowed afterwards
    assert jreq(server, 'POST', '/api/jobs', job_body(server))[0] == 201


def test_runner_receives_crawl_spec(server):
    jreq(server, 'POST', '/api/jobs', job_body(server, cities=['moscow', 'spb'], resume=True))
    spec = FakeRunner.instances[0].spec
    assert spec.mode == 'crawl' and spec.cities == ['moscow', 'spb'] and spec.resume is True
    assert len(spec.rubric_codes) > 100  # all leaf rubrics


def test_rubric_node_expands_to_leaves(server):
    rubrics = jreq(server, 'GET', '/api/rubrics')[1]
    node = next(c for c, r in rubrics.items() if r.get('children'))
    jreq(server, 'POST', '/api/jobs', job_body(server, rubric_code=node))
    codes = FakeRunner.instances[0].spec.rubric_codes
    assert codes and all(not rubrics[c].get('children') for c in codes)


def test_urls_mode_generates_urls(server):
    body = job_body(server, mode='urls', query='Аптека', cities=['moscow'], urls=[])
    assert jreq(server, 'POST', '/api/jobs', body)[0] == 201
    urls = FakeRunner.instances[0].spec.urls
    assert urls == ['https://2gis.ru/moscow/search/аптека/filters/sort=name'.replace('аптека', 'Аптека')]


def test_urls_mode_explicit_urls(server):
    url = 'https://2gis.ru/moscow/search/cafe'
    assert jreq(server, 'POST', '/api/jobs', job_body(server, mode='urls', urls=[url], cities=[]))[0] == 201
    assert FakeRunner.instances[0].spec.urls == [url]


@pytest.mark.parametrize('patch, field', [
    ({'mode': 'x'}, 'mode'),
    ({'format': 'pdf'}, 'format'),
    ({'output_path': ''}, 'output_path'),
    ({'output_path': '/nonexistent-dir-xyz/out.csv'}, 'output_path'),
    ({'cities': []}, 'cities'),
    ({'cities': ['nowhere']}, 'cities'),
    ({'rubric_code': 'zzz'}, 'rubric_code'),
    ({'mode': 'urls', 'urls': [], 'query': '', 'cities': ['moscow']}, 'urls'),
    ({'mode': 'urls', 'urls': ['ftp://x']}, 'urls'),
    ({'resume': 'yes'}, 'resume'),
])
def test_job_validation_422(server, patch, field):
    status, data = jreq(server, 'POST', '/api/jobs', job_body(server, **patch))
    assert status == 422
    assert field in data['errors'] and data['error']
    assert not FakeRunner.instances
    assert jreq(server, 'GET', '/api/jobs/current')[1]['state'] == 'idle'


def test_stop(server):
    jreq(server, 'POST', '/api/jobs', job_body(server))
    status, _ = jreq(server, 'POST', '/api/jobs/current/stop', {})
    assert status == 202
    assert server.jobs.wait(3)
    cur = jreq(server, 'GET', '/api/jobs/current')[1]
    assert cur['state'] == 'done' and 'Остановлено' in cur['message']
    assert FakeRunner.instances[0].stopped


def test_stop_without_job(server):
    status, data = jreq(server, 'POST', '/api/jobs/current/stop', {})
    assert status == 409 and data['error']


def test_captcha_is_separate_state(server):
    server.jobs._runner_factory = lambda spec, config, cb: FakeRunner(spec, config, cb, 'captcha')
    jreq(server, 'POST', '/api/jobs', job_body(server))
    FakeRunner.instances[0].release.set()
    assert server.jobs.wait(3)
    cur = jreq(server, 'GET', '/api/jobs/current')[1]
    assert cur['state'] == 'captcha' and 'капч' in cur['message']


def test_error_state(server):
    server.jobs._runner_factory = lambda spec, config, cb: FakeRunner(spec, config, cb, 'error')
    jreq(server, 'POST', '/api/jobs', job_body(server))
    FakeRunner.instances[0].release.set()
    assert server.jobs.wait(3)
    cur = jreq(server, 'GET', '/api/jobs/current')[1]
    assert cur['state'] == 'error' and cur['message']


# ---- stats ----

def test_stats_404_without_file(server):
    assert jreq(server, 'GET', '/api/stats')[0] == 404
    jreq(server, 'POST', '/api/jobs', job_body(server))
    assert jreq(server, 'GET', '/api/stats')[0] == 404


def test_stats_from_prepared_csv(server):
    out = server.tmp / 'out.csv'
    jreq(server, 'POST', '/api/jobs', job_body(server, output_path=str(out)))
    stats_file = server.tmp / 'out.csv.stats.csv'
    stats_file.write_text('Рубрика,Группа,Всего,Без сайта,Доля без сайта\n'
                          'Аптеки,Здоровье,10,4,0.4\nКафе,Еда,2,2,1.0\n', encoding='utf-8-sig')
    status, data = jreq(server, 'GET', '/api/stats')
    assert status == 200
    assert data['path'] == str(stats_file)
    assert data['rows'][0] == {'rubric': 'Аптеки', 'group': 'Здоровье', 'total': 10, 'without_site': 4, 'share': 0.4}
    assert len(data['rows']) == 2
    assert jreq(server, 'GET', '/api/jobs/current')[1]['stats_available'] is True


# ---- SSE ----

def read_events(srv, want, timeout=5):
    """Read SSE events until `want(events)` is true."""
    conn = http.client.HTTPConnection('127.0.0.1', srv.port, timeout=timeout)
    conn.request('GET', '/api/events')
    resp = conn.getresponse()
    assert resp.status == 200
    assert 'text/event-stream' in resp.getheader('Content-Type')
    events = []
    name = None
    deadline = time.time() + timeout
    try:
        while time.time() < deadline and not want(events):
            line = resp.fp.readline().decode('utf-8')
            if line.startswith('event:'):
                name = line.split(':', 1)[1].strip()
            elif line.startswith('data:'):
                events.append((name, json.loads(line.split(':', 1)[1])))
            elif line.startswith(':'):
                events.append(('keepalive', None))
    finally:
        conn.close()
    return events


def test_sse_status_first_then_backlog(server):
    from parser_2gis.logger import logger
    logger.info('строка до подключения')
    events = read_events(server, lambda ev: any(n == 'log' for n, _ in ev))
    assert events[0][0] == 'status'
    assert events[0][1]['state'] == 'idle'
    logs = [d for n, d in events if n == 'log']
    assert any(d['message'] == 'строка до подключения' and d['level'] == 'INFO' and d['ts'] for d in logs)


def test_sse_live_log_and_status(server):
    from parser_2gis.logger import logger

    def emit():
        time.sleep(0.3)
        logger.info('живая строка')
        jreq(server, 'POST', '/api/jobs', job_body(server))

    threading.Thread(target=emit, daemon=True).start()
    events = read_events(server, lambda ev: any(n == 'status' and d['state'] == 'running' for n, d in ev[1:]))
    assert any(n == 'log' and d['message'] == 'живая строка' for n, d in events)


def test_sse_keepalive(server):
    events = read_events(server, lambda ev: any(n == 'keepalive' for n, _ in ev))
    assert ('keepalive', None) in events


def test_log_ring_buffer_limit(server):
    for i in range(300):
        server.hub.publish_log('INFO', f'line {i}')
    lines = server.hub.log_lines()
    assert len(lines) == 200 and lines[-1]['message'] == 'line 299'


def test_unknown_routes(server):
    assert jreq(server, 'GET', '/api/nothing')[0] == 404
    assert jreq(server, 'POST', '/api/nothing', {})[0] == 404


def test_next_free_port(server):
    other = WebServer(port=server.port, static_dir=server.static_dir, config_path=server.tmp / 'o.config')
    try:
        assert other.port != server.port and other.port > server.port
    finally:
        other.server_close()


def test_crawl_on_progress_callback(tmp_path):
    from parser_2gis.crawler import Checkpoint, CrawlTask, crawl

    class P:
        def __init__(self, url):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def parse(self, writer):
            writer.write({'result': {'items': [{'id': 'x'}]}})

    class W:
        crawl_rubric = None

        def write(self, doc):
            pass

    tasks = [CrawlTask('c', '1', 'u1'), CrawlTask('c', '2', 'u2')]
    calls = []
    cp = Checkpoint(str(tmp_path / 'cp.json'))
    assert crawl(tasks, W(), P, cp, on_progress=lambda *a: calls.append(a)) is True
    assert calls == [(0, 2, 0), (1, 2, 1), (2, 2, 1)]


def test_web_flag_parsing(monkeypatch):
    from parser_2gis.main import parse_arguments

    monkeypatch.setattr('sys.argv', ['p', '--web', '--port', '9000', '--no-browser'])
    args, _ = parse_arguments()
    assert args.web and args.port == 9000 and args.no_browser

    monkeypatch.setattr('sys.argv', ['p', '--web', '--city', 'moscow'])
    with pytest.raises(SystemExit):
        parse_arguments()
    monkeypatch.setattr('sys.argv', ['p', '--web', '-i', 'http://x'])
    with pytest.raises(SystemExit):
        parse_arguments()
