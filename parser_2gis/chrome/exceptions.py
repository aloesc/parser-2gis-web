from pychrome.exceptions import UserAbortException as ChromeUserAbortException
from pychrome.exceptions import RuntimeException as ChromeRuntimeException


class ChromeException(Exception):
    pass


class ChromeCaptchaException(ChromeException):
    """2GIS redirected the browser to the captcha page."""
    def __init__(self, msg: str = 'Обнаружена капча 2GIS (captcha.2gis.ru)', *args, **kwargs) -> None:
        super().__init__(msg, *args, **kwargs)


class ChromePathNotFound(ChromeException):
    def __init__(self, msg: str = 'Chrome браузер не найден', *args, **kwargs) -> None:
        super().__init__(msg, *args, **kwargs)


__all__ = [
    'ChromeUserAbortException',
    'ChromeRuntimeException',
    'ChromeException',
    'ChromePathNotFound',
    'ChromeCaptchaException',
]
