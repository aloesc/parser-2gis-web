from __future__ import annotations

import functools
import glob
import os
import re
import shutil
import socket

from ..common import running_mac, running_windows


def _playwright_build(path: str) -> int:
    """Build number from `.../chromium-<build>/...` (0 if unknown)."""
    match = re.search(r'chromium-(\d+)', path)
    return int(match.group(1)) if match else 0


@functools.lru_cache()
def locate_chrome_path() -> str | None:
    """Locate Chrome's executable path."""
    if running_windows():
        app_dirs = []

        # Win paths from WinAPI
        import ctypes

        csidl = dict(
            CSIDL_PROGRAM_FILES=38,  # C:\Program Files
            CSIDL_PROGRAM_FILESX86=42,  # C:\Program Files (x86)
            CSIDL_LOCAL_APPDATA=28,  # C:\Documents and Settings\<username>\Local Settings\Application Data.
            CSIDL_COMMON_APPDATA=35,  # C:\Documents and Settings\All Users\Application Data
            CSIDL_APPDATA=26,  # C:\Users\<username>
        )

        for _, v in csidl.items():
            buf = ctypes.create_unicode_buffer(1024)
            ctypes.windll.shell32.SHGetFolderPathW(None, v, None, 0, buf)  # type: ignore
            app_dirs.append(buf.value)

        env_dirs = [
            'PROGRAMFILES',
            'PROGRAMFILES(X86)',
            'PROGRAMW6432',
            'LOCALAPPDATA',
        ]

        # Win paths from the environment
        for d in env_dirs:
            if d in os.environ and os.environ[d] not in app_dirs:
                app_dirs.append(os.environ[d])

        # Chrome's possible installation locations
        for path in app_dirs:
            binary_path = os.path.join(path, 'Google', 'Chrome', 'Application', 'chrome.exe')
            if os.path.isfile(binary_path):
                return binary_path

        # We also could try to use Windows registry to find out Chrome's path
        import winreg

        reg_path = r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe'
        for install_type in winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE:  # type: ignore
            try:
                with winreg.OpenKey(install_type, reg_path, 0, winreg.KEY_READ) as reg_key:  # type: ignore
                    binary_path = winreg.QueryValue(reg_key, None)  # type: ignore
                    if os.path.isfile(binary_path):
                        return binary_path
            except WindowsError:  # type: ignore
                continue

    elif running_mac():
        for binary_path in \
                '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', \
                os.path.expanduser('~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'), \
                '/Applications/Chromium.app/Contents/MacOS/Chromium', \
                os.path.expanduser('~/Applications/Chromium.app/Contents/MacOS/Chromium'):
            if os.path.isfile(binary_path):
                return binary_path

    else:
        # Google Chrome has priority over Chromium
        chrome_executables = ['google-chrome', 'chrome', 'chrome-browser', 'google-chrome-stable']
        chromium_executables = ['chromium', 'chromium-browser']
        app_dirs = ['/usr/bin', '/usr/sbin', '/usr/local/bin', '/usr/local/sbin', '/sbin', '/snap/bin',
                    '/opt/google/chrome', '/usr/lib/chromium', '/usr/lib64/chromium-browser',
                    '/usr/lib/chromium-browser']

        for executables in (chrome_executables, chromium_executables):
            for d in app_dirs:
                for f in executables:
                    binary_path = os.path.join(d, f)
                    if os.path.isfile(binary_path):
                        return binary_path

            # We also could look the executable up in PATH
            for f in executables:
                binary_path = shutil.which(f) or ''
                if os.path.isfile(binary_path):
                    return binary_path

        # Last resort: Chromium downloaded by Playwright (the newest build)
        pw_root = os.environ.get('PLAYWRIGHT_BROWSERS_PATH') or os.path.expanduser('~/.cache/ms-playwright')
        builds = glob.glob(os.path.join(pw_root, 'chromium-*', 'chrome-linux*', 'chrome'))
        builds.sort(key=_playwright_build, reverse=True)
        for binary_path in builds:
            if os.path.isfile(binary_path):
                return binary_path

    return None


def free_port() -> int:
    """Get free port using sockets.

    Returns:
        Free port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as free_socket:
        free_socket.bind(('127.0.0.1', 0))
        free_socket.listen(5)
        return free_socket.getsockname()[1]
