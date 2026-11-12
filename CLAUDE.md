# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Обзор

Parser2GIS — парсер 2GIS через Google Chrome (Chrome DevTools Protocol, `pychrome`). Результаты пишутся в CSV/XLSX/JSON. Два интерфейса: CLI (argparse) и GUI (PySimpleGUI, опционально). Интерфейс, логи и сообщения — на русском языке.

## Команды

```bash
pip install -e .[dev]          # dev-зависимости (pytest, tox, pre-commit, pyinstaller); GUI: pip install -e .[gui]
tox                            # flake8 + mypy + pytest на всех версиях Python (3.8–3.11)
tox -e flake8                  # flake8 parser_2gis tests (max-line-length 130)
tox -e mypy                    # mypy==0.950, files = parser_2gis
pytest -v                      # все тесты
pytest tests/test_parser.py -k "csv"   # один тест
python parser-2gis.py -i <URL> -o out.csv -f csv --chrome.headless yes   # запуск CLI
python parser-2gis.py --city kazan -o out.csv -f csv [--rubrics-file codes.txt] [--no-resume]   # обход города по листовым рубрикам
python parser-2gis.py --web [--port 8765] [--no-browser]   # локальный веб-интерфейс (stdlib-сервер, без новых зависимостей)
python setup.py build_standalone       # сборка одного файла через PyInstaller
```

- Тесты в `tests/test_parser.py` — интеграционные: реально открывают Chrome и ходят на `2gis.ru` (нужны установленный Chrome и сеть).
- Версия лежит в `parser_2gis/version.py` (`setup.py` читает её регэкспом). Есть отдельный `config_version` для файла пользовательской конфигурации.
- Код использует `from __future__ import annotations` и pydantic **v1** (`>=1.9,<2.0`) — не переходите на API pydantic v2.
- `scripts/update_cities_list.py` и `scripts/update_rubrics_list.py` обновляют `parser_2gis/data/cities.json` и `rubrics.json`, запуская тот же `ChromeRemote`.

## Архитектура

Поток: `main.py` → `cli`/`gui` app → `runner` → `parser` (+ `chrome`) → `writer`.

- **Точка входа** (`parser_2gis/main.py`): argparse, аргументы вида `--chrome.headless`, `--writer.csv.add-rubrics` разворачиваются через `unwrap_dot_dict` в вложенный dict и валидируются моделью `Configuration`. Если заданы все `-i/-o/-f` — запускается CLI, иначе GUI (если доступен `PySimpleGUI`, флаг `GUI_ENABLED` в `common.py`) с подмешиванием пользовательского конфига (`Configuration.load_config`, `merge_with` учитывает только `__fields_set__`).
- **Configuration** (`config.py`): pydantic-модель из `LogOptions`, `WriterOptions`, `ChromeOptions`, `ParserOptions`. Чтобы добавить настройку, надо описать её в соответствующем `options.py`, добавить CLI-аргумент в `main.py` и (при необходимости) поле в GUI `gui/settings.py`. Справка argparse (`ArgumentHelpFormatter`) берёт значения по умолчанию из `Configuration()`.
- **Runner** (`runner/`): `AbstractRunner` с реализациями `CLIRunner` и GUI-раннером. Раннер открывает один writer на все URL, для каждого URL получает парсер из `get_parser(...)` и вызывает `parser.parse(writer)`.
- **Parser** (`parser/`): `get_parser` перебирает `FirmParser`, `InBuildingParser`, `MainParser` и выбирает по `url_pattern()`; `MainParser` — запасной вариант. `FirmParser` и `InBuildingParser` наследуют `MainParser`. Парсеры не разбирают HTML карточек: они управляют страницей (клики по ссылкам выдачи, пагинация, счётчик XHR через инжектируемый скрипт) и перехватывают JSON-ответы API `catalog.api.2gis.*/.../items/byid` («Catalog Item Document»), которые передают в `writer.write(...)`.
- **Chrome** (`chrome/`): `ChromeBrowser` запускает процесс Chrome с удалённой отладкой и временным профилем; `ChromeRemote` оборачивает `pychrome` (вкладка, навигация, перехват ответов по regex-шаблонам в очередях, блокировка запросов, start-скрипты, DOM-снимок через `dom.py`). Модуль `chrome/patches/` патчит `pychrome` при импорте (`patch_all()`).
- **Writer** (`writer/`): `get_writer` выбирает `CSVWriter`/`XLSXWriter`/`JSONWriter` (наследуют `FileWriter`, используются как контекстные менеджеры). На входе — сырой JSON каталога; `writer/models/` — pydantic-модели его частей. `FileWriter._check_catalog_doc` отсеивает ответы с ошибкой. CSV/XLSX динамически собирают колонки (`columns-per-entity`), умеют удалять пустые колонки и дубликаты по завершении.
- **GUI** (`gui/`): окна PySimpleGUI (генератор ссылок по городам/рубрикам на основе `data/cities.json`, `data/rubrics.json`, редактор URL, настройки). Данные из `parser_2gis/data` упаковываются в сборку PyInstaller через `--add-data`.

## Режим «весь город» и веб-интерфейс

- **Обход** (`parser_2gis/crawler/`, `runner/crawler.py`, `runner/gui.py`): для города × листовых рубрик из `rubrics.json` строится URL `https://2gis.<домен>/<город>/search/x/rubricId/<code>` (slug произвольный) и используется обычный `MainParser`. `crawl()` пишет через `DedupWriter`, ведёт `Checkpoint` (`<output>.checkpoint.json`: done/ids/failed), повторяет упавшую рубрику (2 раза, потом пропуск в `failed`), останавливается на капче (`ChromeCaptchaException`) и по `should_stop`. Парсер сам капчу НЕ решает — только останавливается; решает пользователь.
- **Дедуп**: id из API имеет вид `<id_компании>_<хэш запроса>`, хэш меняется на каждый запрос, поэтому сравнивается только часть до `_` (`normalize_id`). Сравнение по полному id не работает.
- **Возобновление**: `FileWriter` открывает файл в `'w'`, поэтому при продолжении прежний результат переезжает в `<output>.partN`, а новый файл содержит остаток; статистика (`<output>.stats.csv`, для XLSX — лист «Статистика») суммируется по всем частям. Режима дозаписи нет.
- **Колонки/статистика** (`writer/stats.py`, опции `CSVOptions.add_site_flag/add_rubric_codes/add_top_rubric`): «Есть сайт» только по контакту `website`; статистика по ЛИСТОВОЙ рубрике, группа определяется по КОДУ рубрики (названия рубрик в API не всегда совпадают с `rubrics.json`), записи без рубрик API относятся к «рубрике обхода». Компания с несколькими рубриками учитывается в каждой. В `rubrics.json` есть служебный узел `"0"` с `label: null`.
- **Веб-интерфейс** (`parser_2gis/web/`): `ThreadingHTTPServer` на 127.0.0.1, статика в `web/static` (чистые HTML/CSS/JS, без CDN), контракт API описан в `SPEC.md` («Web UI»). Токен `X-Token` на все POST/PUT, проверки Host/Origin. Одна задача одновременно, лог по SSE. При сборке PyInstaller `web/static` добавляется через `--add-data` (`setup.py`).
- **Окружение для проверок**: `mypy` 0.950 из `tox.ini` не запускается на новых Python; свежий mypy даёт 3 старые замечания (`writer/options.py`, `main.py`, `in_building.py`) на чистом HEAD. PySimpleGUI 4.59.0 из `setup.py` отсутствует в PyPI (есть 4.60.5.1) — GUI-код без него не исполнялся.

## Прочее

- Поддерживаемые версии Python: `python_requires>=3.8` в `setup.py`; CI собирает под 3.8–3.11 (в `tox.ini` в `envlist` ещё указан py37 — устарело, в последних коммитах поддержка 3.7 убрана).
- pre-commit: trailing-whitespace, check-json/pretty-format-json (`--no-ensure-ascii --no-sort-keys`), flake8, mypy.
- Flake8 игнорирует `C901,W503,E722,E731`; в `main.py` разрешены длинные строки (E501) для описаний аргументов.
