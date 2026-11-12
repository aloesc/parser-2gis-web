'use strict';
(function () {
  var params = new URLSearchParams(location.search);
  var MOCK = params.get('mock');           // ?mock=1 | captcha | error
  var TOKEN = (document.querySelector('meta[name="token"]') || {}).content || '';
  var $ = function (id) { return document.getElementById(id); };

  function h(tag, attrs) {
    var el = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v == null) return;
      if (k === 'class') el.className = v;
      else if (k === 'text') el.textContent = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return el;
  }

  /* ---------- Toast ---------- */
  function toast(msg, kind) {
    var t = h('div', { class: 'toast ' + (kind || ''), role: kind === 'error' ? 'alert' : 'status', text: msg });
    $('toasts').appendChild(t);
    setTimeout(function () {
      t.classList.add('out');
      setTimeout(function () { t.remove(); }, 250);
    }, kind === 'error' ? 7000 : 3500);
  }

  /* ---------- API ---------- */
  function ApiError(status, data) {
    this.status = status; this.data = data || {};
    this.message = (data && data.error) || ('Ошибка ' + status);
  }
  function api(method, path, body) {
    if (MOCK) return mockApi(method, path, body);
    var opts = { method: method, headers: { 'Accept': 'application/json' } };
    if (method !== 'GET') opts.headers['X-Token'] = TOKEN;
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(path, opts).then(function (r) {
      return r.text().then(function (txt) {
        var data = {};
        try { data = txt ? JSON.parse(txt) : {}; } catch (e) { data = {}; }
        if (!r.ok) throw new ApiError(r.status, data);
        return data;
      });
    }, function () { throw new ApiError(0, { error: 'Нет связи с сервером' }); });
  }
  function fail(e) { toast(e && e.message ? e.message : String(e), 'error'); }

  /* ---------- State ---------- */
  var S = {
    mode: 'urls', format: 'csv', cities: [], countries: [], country: '',
    selCities: new Set(), rubrics: {}, rubric: null, config: {}, status: { state: 'idle' },
    stats: null, statsKey: '', sortKey: 'share', sortDir: -1
  };

  /* ---------- Segmented ---------- */
  function segmented(el, onChange) {
    var btns = Array.prototype.slice.call(el.querySelectorAll('button'));
    function set(v) {
      btns.forEach(function (b) {
        var on = b.dataset.value === v;
        b.setAttribute('aria-checked', on);
        b.tabIndex = on ? 0 : -1;
      });
    }
    btns.forEach(function (b, i) {
      b.addEventListener('click', function () { set(b.dataset.value); onChange(b.dataset.value); });
      b.addEventListener('keydown', function (e) {
        var d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
        if (!d) return;
        e.preventDefault();
        var n = btns[(i + d + btns.length) % btns.length];
        n.focus(); n.click();
      });
    });
    return set;
  }
  var setModeSeg = segmented($('modeSeg'), function (v) { S.mode = v; renderMode(); });
  var setFmtSeg = segmented($('fmtSeg'), function (v) { S.format = v; syncExt(); });
  setModeSeg('urls'); setFmtSeg('csv');

  function renderMode() {
    var crawl = S.mode === 'crawl';
    $('queryField').hidden = crawl;
    $('modeHint').textContent = crawl
      ? 'Парсер обойдёт листовые рубрики выбранных городов без текстового запроса и объединит результаты без дублей.'
      : 'Поиск компаний по запросу в выбранных городах. Рубрика необязательна и уточняет выдачу.';
    $('rubHint').textContent = crawl
      ? 'Необязательно. Выбранный узел ограничит обход листовыми рубриками внутри него; без выбора обходятся все рубрики.'
      : 'Необязательно. Уточняет ссылку поиска выбранной рубрикой.';
    renderRubricChip();
  }

  function syncExt() {
    var p = $('outPath').value;
    var m = /\.(csv|xlsx|json)$/i.exec(p);
    if (m && m[1].toLowerCase() !== S.format) $('outPath').value = p.slice(0, -m[0].length) + '.' + S.format;
  }

  /* ---------- Cities ---------- */
  function visibleCities() {
    var q = $('citySearch').value.trim().toLowerCase();
    return S.cities.filter(function (c) {
      return (!S.country || c.country_code === S.country) && (!q || c.name.toLowerCase().indexOf(q) >= 0 || c.code.indexOf(q) >= 0);
    });
  }
  function renderCities() {
    var list = $('cityList'), items = visibleCities();
    list.textContent = '';
    if (!items.length) { list.appendChild(h('div', { class: 'empty', text: 'Города не найдены' })); }
    var frag = document.createDocumentFragment();
    items.forEach(function (c) {
      var cb = h('input', { type: 'checkbox', value: c.code });
      cb.checked = S.selCities.has(c.code);
      cb.addEventListener('change', function () {
        if (cb.checked) S.selCities.add(c.code); else S.selCities.delete(c.code);
        renderCityCount();
      });
      frag.appendChild(h('label', { class: 'check-item' }, cb, h('span', { text: c.name }), h('span', { class: 'sub', text: c.code })));
    });
    list.appendChild(frag);
    renderCityCount();
  }
  function renderCityCount() { $('cityCount').textContent = 'Выбрано: ' + S.selCities.size; }
  $('citySearch').addEventListener('input', renderCities);
  $('cityAll').addEventListener('click', function () { visibleCities().forEach(function (c) { S.selCities.add(c.code); }); renderCities(); });
  $('cityNone').addEventListener('click', function () { visibleCities().forEach(function (c) { S.selCities.delete(c.code); }); renderCities(); });
  $('country').addEventListener('change', function () { S.country = this.value; renderCities(); renderRubrics(); });

  /* ---------- Rubrics ---------- */
  function rubricAllowed(r) {
    if (!S.country) return true;
    return S.country === 'ru' ? r.isRussian !== false : r.isNonRussian !== false;
  }
  function rubricRoots() {
    return Object.keys(S.rubrics).map(function (k) { return S.rubrics[k]; }).filter(function (r) {
      return (!r.parentCode || r.parentCode === '0' || !S.rubrics[r.parentCode]) && rubricAllowed(r);
    });
  }
  function rubricPath(r) {
    var parts = [], cur = S.rubrics[r.parentCode], guard = 0;
    while (cur && guard++ < 10) { parts.unshift(cur.label); cur = S.rubrics[cur.parentCode]; }
    return parts.join(' / ');
  }
  function selectRubric(code) { S.rubric = code; renderRubricChip(); renderRubrics(true); }
  function renderRubricChip() {
    var box = $('rubCurrent'), r = S.rubric && S.rubrics[S.rubric];
    box.textContent = '';
    $('rubReset').disabled = !r;
    if (!r) return;
    var leaf = !(r.children && r.children.length);
    box.appendChild(h('div', { class: 'chip' },
      h('span', { text: r.label + (S.mode === 'crawl' ? (leaf ? '' : ' · все подрубрики') : '') }),
      h('button', { type: 'button', 'aria-label': 'Убрать рубрику', onclick: function () { selectRubric(null); }, text: '×' })));
  }
  $('rubReset').addEventListener('click', function () { selectRubric(null); });

  function rubricRow(r, opts) {
    var kids = (r.children || []).filter(function (c) { return S.rubrics[c] && rubricAllowed(S.rubrics[c]); });
    var li = h('li');
    var row = h('div', { class: 'tree-row' + (S.rubric === r.code ? ' selected' : '') });
    var sub = null;
    if (kids.length && !opts.flat) {
      var tg = h('button', { type: 'button', class: 'tree-toggle', 'aria-expanded': 'false', 'aria-label': 'Раскрыть: ' + r.label });
      tg.addEventListener('click', function () {
        var open = tg.getAttribute('aria-expanded') === 'true';
        tg.setAttribute('aria-expanded', !open);
        if (!open && !sub) {                     // ленивое построение
          sub = h('ul');
          kids.forEach(function (c) { sub.appendChild(rubricRow(S.rubrics[c], {})); });
          li.appendChild(sub);
        }
        if (sub) sub.hidden = open;
      });
      row.appendChild(tg);
    } else row.appendChild(h('span', { class: 'tree-spacer' }));
    var lab = h('button', { type: 'button', class: 'tree-label', 'aria-pressed': S.rubric === r.code },
      r.label, kids.length ? h('span', { class: 'sub', text: String(kids.length) }) : null,
      opts.flat ? h('span', { class: 'tree-path', text: rubricPath(r) }) : null);
    lab.addEventListener('click', function () { selectRubric(S.rubric === r.code ? null : r.code); });
    row.appendChild(lab);
    li.appendChild(row);
    return li;
  }
  function renderRubrics() {
    var box = $('rubTree'), q = $('rubSearch').value.trim().toLowerCase();
    box.textContent = '';
    if (!Object.keys(S.rubrics).length) { box.appendChild(h('div', { class: 'empty', text: 'Рубрики загружаются…' })); return; }
    var ul = h('ul', { class: 'tree' });
    if (q) {
      var found = Object.keys(S.rubrics).map(function (k) { return S.rubrics[k]; })
        .filter(function (r) { return rubricAllowed(r) && r.label.toLowerCase().indexOf(q) >= 0; });
      found.slice(0, 100).forEach(function (r) { ul.appendChild(rubricRow(r, { flat: true })); });
      if (!found.length) box.appendChild(h('div', { class: 'empty', text: 'Ничего не найдено' }));
      else if (found.length > 100) ul.appendChild(h('li', { class: 'empty', text: 'Показаны первые 100 из ' + found.length + '. Уточните запрос.' }));
    } else rubricRoots().forEach(function (r) { ul.appendChild(rubricRow(r, {})); });
    box.appendChild(ul);
  }
  var rubTimer;
  $('rubSearch').addEventListener('input', function () { clearTimeout(rubTimer); rubTimer = setTimeout(renderRubrics, 150); });

  /* ---------- Settings ---------- */
  var FIELDS = [
    ['Chrome'],
    ['chrome.headless', 'bool', 'Скрытый режим', 'Браузер без окна'],
    ['chrome.binary_path', 'path', 'Путь к Chrome', 'Пусто — найти автоматически'],
    ['chrome.memory_limit', 'int', 'Лимит памяти, МБ', 'Ограничение V8'],
    ['Парсер'],
    ['parser.max_records', 'int', 'Максимум записей', 'С одного URL'],
    ['parser.delay_between_clicks', 'int', 'Задержка между кликами, мс', ''],
    ['parser.use_gc', 'bool', 'Сборщик мусора', 'Снижает расход памяти'],
    ['parser.skip_404_response', 'bool', 'Пропускать ответы 404', ''],
    ['Запись'],
    ['writer.encoding', 'text', 'Кодировка', 'Например, utf-8-sig'],
    ['writer.verbose', 'bool', 'Выводить названия в журнал', ''],
    ['writer.csv.add_rubrics', 'bool', 'Добавлять рубрики', ''],
    ['writer.csv.add_comments', 'bool', 'Добавлять комментарии', 'К телефонам, e-mail и т. п.'],
    ['writer.csv.columns_per_entity', 'int', 'Колонок на сущность', 'От 1 до 5'],
    ['writer.csv.remove_empty_columns', 'bool', 'Удалять пустые колонки', ''],
    ['writer.csv.remove_duplicates', 'bool', 'Удалять дубликаты', ''],
    ['writer.csv.join_char', 'text', 'Разделитель значений', ''],
    ['writer.csv.add_site_flag', 'bool', 'Колонка «Есть сайт»', ''],
    ['writer.csv.add_rubric_codes', 'bool', 'Колонки «Коды рубрик» и «Рубрика обхода»', 'Нужны для статистики по группам']
  ];
  var fieldEls = {};
  function getPath(o, p) { return p.split('.').reduce(function (a, k) { return a == null ? undefined : a[k]; }, o); }
  function setPath(o, p, v) {
    var ks = p.split('.'), c = o;
    ks.slice(0, -1).forEach(function (k) { c = c[k] = c[k] || {}; });
    c[ks[ks.length - 1]] = v;
  }
  function buildSettings() {
    var box = $('settingsFields'); box.textContent = ''; fieldEls = {};
    FIELDS.forEach(function (f, i) {
      if (f.length === 1) { box.appendChild(h('div', { class: 'set-group', text: f[0] })); return; }
      var p = f[0], type = f[1], val = getPath(S.config, p), id = 'set-' + i;
      var err = h('div', { class: 'field-error', id: id + '-err', hidden: true, role: 'alert' });
      var name = h('div', { class: 'name' }, h('div', { text: f[2] }), f[3] ? h('div', { class: 'desc', text: f[3] }) : null);
      var ctl, row;
      if (type === 'bool') {
        ctl = h('input', { type: 'checkbox', role: 'switch', id: id });
        ctl.checked = !!val;
        row = h('label', { class: 'set-line', for: id }, name, h('span', { class: 'switch' }, ctl, h('span', { class: 'slider', 'aria-hidden': 'true' })));
      } else {
        ctl = h('input', { type: type === 'int' ? 'number' : 'text', id: id, class: 'input' + (type === 'path' ? ' wide mono' : ''), 'aria-describedby': id + '-err', autocomplete: 'off' });
        if (type === 'int') ctl.min = '0';
        ctl.value = val == null ? '' : val;
        row = h('div', { class: 'set-line' }, h('label', { for: id, class: 'name' }, h('div', { text: f[2] }), f[3] ? h('div', { class: 'desc', text: f[3] }) : null), ctl);
      }
      var wrap = h('div', { class: 'set-row' }, row, err);
      box.appendChild(wrap);
      fieldEls[p] = { ctl: ctl, type: type, err: err };
    });
  }
  function collectSettings() {
    var body = {};
    Object.keys(fieldEls).forEach(function (p) {
      var f = fieldEls[p], v;
      if (f.type === 'bool') v = f.ctl.checked;
      else if (f.type === 'int') v = f.ctl.value === '' ? null : Number(f.ctl.value);
      else if (f.type === 'path') v = f.ctl.value.trim() || null;
      else v = f.ctl.value;
      if (v === null && f.type === 'int') return;   // пустое число не отправляем
      setPath(body, p, v);
    });
    return body;
  }
  function clearFieldErrors() {
    Object.keys(fieldEls).forEach(function (p) {
      fieldEls[p].err.hidden = true; fieldEls[p].err.textContent = '';
      fieldEls[p].ctl.classList.remove('invalid'); fieldEls[p].ctl.removeAttribute('aria-invalid');
    });
  }
  function showFieldErrors(errors) {
    var rest = [];
    Object.keys(errors || {}).forEach(function (k) {
      var key = k.replace(/\[\d+\]/g, ''), hit = null;
      Object.keys(fieldEls).forEach(function (p) {
        if (p === key || key.slice(-p.length - 1) === '.' + p || p.slice(-key.length - 1) === '.' + key) hit = p;
      });
      if (hit) {
        var f = fieldEls[hit];
        f.err.textContent = errors[k]; f.err.hidden = false;
        f.ctl.classList.add('invalid'); f.ctl.setAttribute('aria-invalid', 'true');
      } else rest.push(k + ': ' + errors[k]);
    });
    if (rest.length) toast(rest.join('\n'), 'error');
  }
  $('settingsToggle').addEventListener('click', function () {
    var open = this.getAttribute('aria-expanded') === 'true';
    this.setAttribute('aria-expanded', !open);
    $('settingsBody').hidden = open;
  });
  $('saveSettings').addEventListener('click', function () {
    var btn = this; btn.disabled = true; clearFieldErrors();
    api('PUT', '/api/config', collectSettings()).then(function (r) {
      S.config = r.config || S.config; buildSettings(); toast('Настройки сохранены', 'ok');
    }).catch(function (e) {
      if (e.status === 422 && e.data.errors) { showFieldErrors(e.data.errors); toast('Проверьте значения настроек', 'error'); }
      else fail(e);
    }).then(function () { btn.disabled = false; });
  });

  /* ---------- Job ---------- */
  var LABELS = { idle: 'Ожидание', running: 'Выполняется', stopping: 'Остановка…', done: 'Готово', error: 'Ошибка', captcha: 'Капча' };
  var BUSY = { running: 1, stopping: 1 };

  function applyStatus(st) {
    var prev = S.status || {};
    S.status = st || { state: 'idle' };
    st = S.status;
    var state = st.state || 'idle';
    $('statusPill').dataset.state = state;
    $('statusText').textContent = LABELS[state] || state;
    $('captchaBanner').hidden = state !== 'captcha';
    $('errorBanner').hidden = state !== 'error';
    if (state === 'error') $('errorText').textContent = st.message || 'Неизвестная ошибка. Подробности в журнале.';

    var btn = $('runBtn');
    btn.classList.remove('btn-primary', 'btn-danger');
    if (state === 'running') { btn.textContent = 'Остановить'; btn.classList.add('btn-danger'); btn.disabled = false; }
    else if (state === 'stopping') { btn.textContent = 'Останавливаю…'; btn.classList.add('btn-danger'); btn.disabled = true; }
    else { btn.textContent = state === 'idle' ? 'Запустить' : 'Запустить снова'; btn.classList.add('btn-primary'); btn.disabled = false; }

    var busy = !!BUSY[state];
    ['query', 'outPath', 'resume', 'citySearch', 'rubSearch'].forEach(function (id) { $(id).disabled = busy && id !== 'citySearch' && id !== 'rubSearch'; });
    $('modeSeg').setAttribute('aria-disabled', busy); $('fmtSeg').setAttribute('aria-disabled', busy);

    var p = st.progress || { done: 0, total: 0, records: 0 };
    var show = busy || state === 'done' || state === 'captcha' || (state === 'error' && p.records);
    $('progressWrap').hidden = !show;
    var pct = p.total ? Math.min(100, Math.round(p.done / p.total * 100)) : 0;
    var bar = $('progress');
    bar.classList.toggle('indeterminate', state === 'running' && !p.total);
    bar.setAttribute('aria-valuenow', pct);
    $('progressBar').style.width = (state === 'done' && !p.total ? 100 : pct) + '%';
    $('progressText').textContent = p.done + ' / ' + (p.total || '?') + (p.total ? ' (' + pct + '%)' : '');
    $('recordsText').textContent = 'Записей: ' + (p.records || 0);
    $('runMessage').textContent = state === 'error' ? '' : (st.message || '');

    if (st.stats_available) {
      var key = (st.id || '') + ':' + state;
      if (key !== S.statsKey) { S.statsKey = key; loadStats(); }
    } else { S.stats = null; S.statsKey = ''; $('statsCard').hidden = true; }
  }

  $('runBtn').addEventListener('click', function () {
    var state = S.status.state;
    if (state === 'running') {
      api('POST', '/api/jobs/current/stop').then(function () { toast('Останавливаю задачу…'); }).catch(fail);
      return;
    }
    var path = $('outPath').value.trim(), q = $('query').value.trim(), crawl = S.mode === 'crawl';
    if (!S.selCities.size) return toast('Выберите хотя бы один город', 'error');
    if (!crawl && !q) { $('query').focus(); return toast('Введите запрос', 'error'); }
    if (!path) { $('outPath').focus(); return toast('Укажите файл результата', 'error'); }
    var body = {
      mode: S.mode, urls: [], query: crawl ? '' : q, cities: Array.from(S.selCities),
      rubric_code: S.rubric || null, output_path: path, format: S.format, resume: $('resume').checked
    };
    var btn = this; btn.disabled = true;
    api('POST', '/api/jobs', body).then(function () {
      return api('GET', '/api/jobs/current').then(applyStatus);
    }).catch(function (e) {
      if (e.status === 422 && e.data.errors) toast(Object.keys(e.data.errors).map(function (k) { return e.data.errors[k]; }).join('\n'), 'error');
      else fail(e);
      applyStatus(S.status);
    });
  });

  /* ---------- Stats ---------- */
  function loadStats() {
    api('GET', '/api/stats').then(function (d) {
      S.stats = d.rows || []; 
      var mx = S.stats.reduce(function (m, r) { return Math.max(m, r.share || 0); }, 0);
      S.statsPct = mx > 1;
      $('statsPath').textContent = d.path ? 'Файл: ' + d.path : '';
      $('statsCard').hidden = false; renderStats();
    }).catch(function (e) { S.stats = null; $('statsCard').hidden = true; if (e.status !== 404) fail(e); });
  }
  function shareOf(r) { var s = r.share || 0; return S.statsPct ? s / 100 : s; }
  function renderStats() {
    var q = $('statsSearch').value.trim().toLowerCase(), k = S.sortKey, d = S.sortDir;
    var rows = (S.stats || []).filter(function (r) {
      return !q || (r.rubric || '').toLowerCase().indexOf(q) >= 0 || (r.group || '').toLowerCase().indexOf(q) >= 0;
    });
    rows.sort(function (a, b) {
      var x = a[k], y = b[k];
      if (typeof x === 'string' || typeof y === 'string') return String(x || '').localeCompare(String(y || ''), 'ru') * d;
      return ((x || 0) - (y || 0)) * d;
    });
    var body = $('statsBody'); body.textContent = '';
    rows.forEach(function (r) {
      var sh = shareOf(r), pct = Math.round(sh * 1000) / 10;
      var fill = h('div', { class: 'share-fill' }); fill.style.width = Math.min(100, sh * 100) + '%';
      body.appendChild(h('tr', { class: sh >= 0.7 ? 'high' : '' },
        h('td', { text: r.rubric }), h('td', { class: 'group', text: r.group || '' }),
        h('td', { class: 'num', text: String(r.total) }), h('td', { class: 'num', text: String(r.without_site) }),
        h('td', null, h('div', { class: 'share' }, h('div', { class: 'share-track' }, fill), h('span', { class: 'share-val', text: pct + '%' })))));
    });
    $('statsEmpty').hidden = rows.length > 0;
    Array.prototype.forEach.call($('statsTable').querySelectorAll('th'), function (th) {
      th.setAttribute('aria-sort', th.firstElementChild.dataset.key === k ? (d > 0 ? 'ascending' : 'descending') : 'none');
    });
  }
  Array.prototype.forEach.call($('statsTable').querySelectorAll('th button'), function (b) {
    b.addEventListener('click', function () {
      var key = b.dataset.key;
      if (S.sortKey === key) S.sortDir = -S.sortDir; else { S.sortKey = key; S.sortDir = (key === 'rubric' || key === 'group') ? 1 : -1; }
      renderStats();
    });
  });
  $('statsSearch').addEventListener('input', renderStats);

  /* ---------- Log ---------- */
  var stick = true, MAXLOG = 2000;
  var logEl = $('log');
  function fmtTs(ts) {
    if (!ts) return '';
    var m = /T(\d\d:\d\d:\d\d)/.exec(ts) || /(\d\d:\d\d:\d\d)/.exec(ts);
    return m ? m[1] : String(ts);
  }
  function addLog(l) {
    var lv = String(l.level || 'info').toLowerCase();
    logEl.appendChild(h('div', { class: 'log-line l-' + lv },
      h('span', { class: 'ts', text: fmtTs(l.ts) }), h('span', { class: 'lv', text: lv === 'warning' ? '[WARN] ' : '' }), String(l.message == null ? '' : l.message)));
    while (logEl.childNodes.length > MAXLOG) logEl.removeChild(logEl.firstChild);
    if (stick) logEl.scrollTop = logEl.scrollHeight;
  }
  logEl.addEventListener('scroll', function () {
    stick = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 30;
    $('logJump').hidden = stick;
  });
  $('logJump').addEventListener('click', function () { stick = true; logEl.scrollTop = logEl.scrollHeight; this.hidden = true; });
  $('logClear').addEventListener('click', function () { logEl.textContent = ''; stick = true; $('logJump').hidden = true; });

  function connect() {
    var es = MOCK ? new MockES() : new EventSource('/api/events');
    es.addEventListener('open', function () { setConn(true); });
    es.addEventListener('error', function () { setConn(false); });   // EventSource переподключается сам
    es.addEventListener('log', function (e) { try { addLog(JSON.parse(e.data)); } catch (x) { /* ignore */ } });
    es.addEventListener('status', function (e) { try { applyStatus(JSON.parse(e.data)); } catch (x) { /* ignore */ } });
  }
  function setConn(ok) { var c = $('conn'); c.dataset.ok = ok ? '1' : '0'; c.textContent = ok ? 'подключено' : 'нет связи'; }

  /* ---------- Init ---------- */
  function init() {
    renderMode(); renderCities(); renderRubrics(); applyStatus({ state: 'idle' });
    Promise.all([api('GET', '/api/bootstrap'), api('GET', '/api/rubrics')]).then(function (res) {
      var b = res[0];
      $('version').textContent = b.version ? 'v' + b.version : '';
      S.cities = b.cities || []; S.countries = b.countries || []; S.config = b.config || {};
      // rubrics.json has a service node "0" with `label: null`; drop nodes without a label
      S.rubrics = {};
      Object.keys(res[1] || {}).forEach(function (k) { if (res[1][k] && res[1][k].label) S.rubrics[k] = res[1][k]; });
      var sel = $('country'); sel.textContent = '';
      sel.appendChild(h('option', { value: '', text: 'Все страны' }));
      S.countries.forEach(function (c) { sel.appendChild(h('option', { value: c.code, text: c.name })); });
      var def = S.countries.some(function (c) { return c.code === 'ru'; }) ? 'ru' : '';
      sel.value = def; S.country = def;
      $('outPath').value = b.default_output_path || '';
      syncExt();
      renderCities(); renderRubrics(); buildSettings();
      return api('GET', '/api/jobs/current').then(applyStatus);
    }).catch(fail).then(connect);
  }

  /* ---------- Mock ---------- */
  var bus = [];
  function emit(ev, data) { bus.forEach(function (fn) { fn(ev, data); }); }
  function MockES() {
    var ls = {}; var self = this;
    this.addEventListener = function (ev, fn) { (ls[ev] = ls[ev] || []).push(fn); };
    this.close = function () { bus.splice(bus.indexOf(handler), 1); };
    function handler(ev, data) { (ls[ev] || []).forEach(function (fn) { fn({ data: JSON.stringify(data) }); }); }
    bus.push(handler);
    setTimeout(function () {
      (ls.open || []).forEach(function (fn) { fn({}); });
      handler('status', mockJob);
      ['Parser2GIS запущен (демо-режим)', 'Chrome найден автоматически'].forEach(function (m) { handler('log', mockLog('info', m)); });
    }, 50);
  }
  function mockLog(level, message) { return { ts: new Date().toISOString(), level: level, message: message }; }
  var mockJob = { id: null, state: 'idle', progress: { done: 0, total: 0, records: 0 }, message: '', stats_available: false };
  var mockTimer = null;

  function mockData() {
    var countries = [{ code: 'ru', name: 'Россия' }, { code: 'by', name: 'Беларусь' }, { code: 'kz', name: 'Казахстан' }];
    var names = [['Москва', 'moscow', 'ru'], ['Санкт-Петербург', 'spb', 'ru'], ['Новосибирск', 'novosibirsk', 'ru'], ['Екатеринбург', 'ekaterinburg', 'ru'],
      ['Казань', 'kazan', 'ru'], ['Сочи', 'sochi', 'ru'], ['Омск', 'omsk', 'ru'], ['Минск', 'minsk', 'by'], ['Гомель', 'gomel', 'by'],
      ['Алматы', 'almaty', 'kz'], ['Астана', 'astana', 'kz']];
    for (var i = 1; i <= 40; i++) names.push(['Город-демо ' + i, 'demo' + i, 'ru']);
    var R = {};
    function add(code, label, parent, kids, ru, nonru) { R[code] = { code: code, label: label, parentCode: parent, children: kids || [], isRussian: ru !== false, isNonRussian: nonru !== false }; }
    add('1', 'Еда', '0', ['11', '12', '13']); add('11', 'Кафе и рестораны', '1', ['111', '112']); add('111', 'Рестораны', '11'); add('112', 'Кофейни', '11');
    add('12', 'Фастфуд', '1'); add('13', 'Доставка еды', '1', null, true, false);
    add('2', 'Медицина', '0', ['21', '22']); add('21', 'Стоматологии', '2', ['211', '212']); add('211', 'Частные стоматологии', '21'); add('212', 'Детские стоматологии', '21');
    add('22', 'Аптеки', '2');
    add('3', 'Красота', '0', ['31', '32']); add('31', 'Салоны красоты', '3'); add('32', 'Барбершопы', '3');
    var kids = []; for (var j = 0; j < 30; j++) { add('9' + j, 'Автосервис, тип ' + (j + 1), '4'); kids.push('9' + j); }
    add('4', 'Авто', '0', kids);
    add('5', 'Только за рубежом', '0', null, false, true);
    return {
      boot: {
        version: '1.0.0-demo', default_output_path: '/home/user/Документы/parser2gis/result.csv', countries: countries,
        cities: names.map(function (n) { return { name: n[0], code: n[1], domain: n[2], country_code: n[2] }; }),
        config: mockCfg()
      }, rubrics: R
    };
  }
  function mockCfg() {
    return mockCfg.v || (mockCfg.v = {
      chrome: { binary_path: null, headless: false, memory_limit: 3000, start_maximized: false },
      parser: { skip_404_response: true, delay_between_clicks: 0, max_records: 1200, use_gc: false, gc_pages_interval: 10 },
      writer: { encoding: 'utf-8-sig', verbose: true, csv: { add_rubrics: true, add_comments: true, columns_per_entity: 3, remove_empty_columns: true, remove_duplicates: true, join_char: '; ', add_site_flag: true, add_rubric_codes: true } }
    });
  }
  function mockStats() {
    var rows = [['Частные стоматологии', 'Стоматологии', 412, 37], ['Детские стоматологии', 'Стоматологии', 96, 41], ['Аптеки', 'Медицина', 1204, 380],
      ['Рестораны', 'Кафе и рестораны', 880, 120], ['Кофейни', 'Кафе и рестораны', 540, 301], ['Барбершопы', 'Красота', 210, 168], ['Салоны красоты', 'Красота', 975, 702],
      ['Фастфуд', 'Еда', 330, 61], ['Автосервис, тип 1', 'Авто', 150, 117], ['Автосервис, тип 2', 'Авто', 88, 70]];
    return { path: '/home/user/Документы/parser2gis/result.csv.stats.csv', rows: rows.map(function (r) { return { rubric: r[0], group: r[1], total: r[2], without_site: r[3], share: r[3] / r[2] }; }) };
  }
  function mockSet(st) {
    mockJob = Object.assign({}, mockJob, st); emit('status', mockJob);
  }
  function mockRun(body) {
    var total = 8, i = 0, id = 'job-' + Date.now();
    mockJob = { id: id, state: 'running', mode: body.mode, output_path: body.output_path, format: body.format, started_at: new Date().toISOString(), progress: { done: 0, total: total, records: 0 }, message: '', stats_available: false };
    emit('status', mockJob);
    emit('log', mockLog('info', 'Запуск: режим ' + body.mode + ', городов: ' + body.cities.length));
    mockTimer = setInterval(function () {
      i++;
      var recs = i * 53;
      emit('log', mockLog(i % 5 === 0 ? 'warning' : i === 3 ? 'debug' : 'info', 'Рубрика ' + i + '/' + total + ': собрано записей ' + recs));
      if (MOCK === 'captcha' && i === 4) { clearInterval(mockTimer); emit('log', mockLog('error', 'Редирект на captcha.2gis.ru')); mockSet({ state: 'captcha', progress: { done: i, total: total, records: recs }, message: 'Капча' }); return; }
      if (MOCK === 'error' && i === 4) { clearInterval(mockTimer); emit('log', mockLog('error', 'Chrome неожиданно завершился')); mockSet({ state: 'error', progress: { done: i, total: total, records: recs }, message: 'Chrome неожиданно завершился. Запустите задачу снова с «Продолжить с места остановки».' }); return; }
      if (i >= total) { clearInterval(mockTimer); emit('log', mockLog('info', 'Готово')); mockSet({ state: 'done', finished_at: new Date().toISOString(), progress: { done: total, total: total, records: recs }, stats_available: body.mode === 'crawl', message: 'Результат сохранён' }); return; }
      mockSet({ progress: { done: i, total: total, records: recs } });
    }, 900);
  }
  function mockApi(method, path, body) {
    return new Promise(function (resolve, reject) {
      setTimeout(function () {
        if (path === '/api/bootstrap') return resolve(mockData().boot);
        if (path === '/api/rubrics') return resolve(mockData().rubrics);
        if (path === '/api/config' && method === 'PUT') {
          var c = body.writer && body.writer.csv, errs = {};
          if (c && (c.columns_per_entity < 1 || c.columns_per_entity > 5)) errs['writer.csv.columns_per_entity'] = 'Значение должно быть от 1 до 5';
          if (body.chrome && body.chrome.memory_limit < 1) errs['chrome.memory_limit'] = 'Значение должно быть больше 0';
          if (Object.keys(errs).length) return reject(new ApiError(422, { errors: errs }));
          ['chrome', 'parser'].forEach(function (k) { Object.assign(mockCfg()[k], body[k]); });
          Object.assign(mockCfg().writer, body.writer, { csv: Object.assign(mockCfg().writer.csv, body.writer && body.writer.csv) });
          return resolve({ config: mockCfg() });
        }
        if (path === '/api/jobs' && method === 'POST') {
          if (mockJob.state === 'running') return reject(new ApiError(409, { error: 'Задача уже выполняется' }));
          mockRun(body); return resolve({ id: mockJob.id });
        }
        if (path === '/api/jobs/current') return resolve(mockJob);
        if (path === '/api/jobs/current/stop') {
          clearInterval(mockTimer); mockSet({ state: 'stopping' });
          setTimeout(function () { mockSet({ state: 'done', message: 'Остановлено пользователем' }); }, 1500);
          return resolve({});
        }
        if (path === '/api/stats') return resolve(mockStats());
        reject(new ApiError(404, { error: 'Не найдено' }));
      }, 120);
    });
  }
  if (MOCK) {
    var bar = h('div', { class: 'mockbar' }, h('span', { text: 'Демо:' }));
    var sel = h('select', { 'aria-label': 'Принудительное состояние' });
    ['idle', 'running', 'stopping', 'done', 'error', 'captcha', 'done + статистика'].forEach(function (s) { sel.appendChild(h('option', { value: s, text: s })); });
    sel.addEventListener('change', function () {
      clearInterval(mockTimer);
      var v = sel.value, stats = v === 'done + статистика';
      mockSet({ state: stats ? 'done' : v, id: 'forced-' + v, stats_available: stats, message: v === 'error' ? 'Демонстрационная ошибка' : '', progress: { done: v === 'idle' ? 0 : 5, total: v === 'idle' ? 0 : 8, records: v === 'idle' ? 0 : 265 } });
    });
    bar.appendChild(sel); document.body.appendChild(bar);
  }

  init();
})();
