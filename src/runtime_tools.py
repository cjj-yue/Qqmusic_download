"""Locate bundled tools and optional user overrides."""
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parent
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ROOT.parent


def find_tool(name):
    override = os.environ.get('QQMUSIC_' + name.upper())
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return str(path.resolve())
        raise RuntimeError('configured_' + name + '_not_found')
    bundled = Path(getattr(sys, '_MEIPASS', ROOT)) / 'bin'
    for directory in (bundled, APP_DIR / 'bin', APP_DIR):
        path = directory / (name + '.exe')
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(name + '_not_installed')
