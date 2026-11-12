from __future__ import annotations

import pathlib
import threading
from typing import Any, Dict, Optional

from pydantic import ValidationError

from ..common import report_from_validation_error
from ..config import Configuration
from ..paths import user_path

EDITABLE_SECTIONS = ('chrome', 'parser', 'writer')


class ConfigValidationError(Exception):
    def __init__(self, errors: Dict[str, str]) -> None:
        super().__init__('; '.join(f'{k}: {v}' for k, v in errors.items()))
        self.errors = errors


def _deep_merge(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class ConfigStore:
    """User configuration (the same file the desktop GUI uses)."""
    def __init__(self, config_path: Optional[pathlib.Path] = None) -> None:
        self._path = config_path
        self._lock = threading.Lock()
        self._config = Configuration.load_config(config_path, auto_create=True)

    def get(self) -> Configuration:
        with self._lock:
            return self._config.copy(deep=True)

    def public(self) -> Dict[str, Any]:
        """Editable part of the configuration (without `log` and `path`)."""
        import json
        with self._lock:
            return json.loads(self._config.json(include=set(EDITABLE_SECTIONS)))  # type: ignore[no-any-return]

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """Merge `patch` into the configuration, validate and save it.

        Raises:
            ConfigValidationError: Invalid values; the stored configuration stays untouched.
        """
        unknown = {k: 'Неизвестный раздел конфигурации' for k in patch if k not in EDITABLE_SECTIONS}
        if unknown:
            raise ConfigValidationError(unknown)
        for key, value in patch.items():
            if not isinstance(value, dict):
                raise ConfigValidationError({key: 'Ожидается объект'})

        with self._lock:
            merged = _deep_merge(self._config.dict(exclude={'path'}), patch)
            try:
                new_config = Configuration(**merged)
            except ValidationError as e:
                report = report_from_validation_error(e)
                raise ConfigValidationError({k: v['error_message'] for k, v in report.items()})

            new_config.path = self._config.path or self._path or (user_path() / 'parser-2gis.config')
            new_config.save_config()
            self._config = new_config
        return self.public()
