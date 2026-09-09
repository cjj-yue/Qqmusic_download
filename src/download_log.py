"""One append-only UTF-8 download log per destination directory."""
from datetime import datetime
from pathlib import Path
import threading

LOG_NAME = '下载.log'
_LOCK = threading.Lock()


def append_log(folder, event, **fields):
    # One event per line, even when remote song metadata contains newlines.
    def clean(value):
        return str(value).replace('\r', r'\r').replace('\n', r'\n').replace('\t', ' ')
    line = datetime.now().strftime('%Y-%m-%d %H:%M:%S') + ' [' + clean(event) + ']'
    if fields:
        line += ' ' + ' | '.join(clean(key) + '=' + clean(value) for key, value in fields.items())
    try:
        with _LOCK:
            with (Path(folder) / LOG_NAME).open('a', encoding='utf-8') as stream:
                stream.write(line + '\n')
        return True
    except OSError:
        # A log write failure must not turn a verified audio file into a failed download.
        return False
