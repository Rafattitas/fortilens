"""Small shared safety primitives. Never pass exception text to a browser."""
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import time

PORTABLE = Path(__file__).resolve().parents[1] / 'portable'
if str(PORTABLE) not in sys.path:
    sys.path.insert(0, str(PORTABLE))
spec = importlib.util.spec_from_file_location('fortilens_portable_setup', PORTABLE / 'setup.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class Problem(Exception):
    def __init__(self, code, message, status=400):
        self.code, self.message, self.status = code, message, status
        super().__init__(code)


def report_failure(event, exc):
    """Log a fixed event and coarse category, never exception/input contents.

    Callers supply a source-code event constant, not a path, request or user value.
    Diagnostics are best effort and must not replace the operational failure.
    """
    if isinstance(exc, sqlite3.Error):
        category = 'storage'
    elif isinstance(exc, OSError):
        category = 'io'
    elif isinstance(exc, (ValueError, TypeError, KeyError, setup.SetupError)):
        category = 'invalid_data'
    else:
        category = 'unexpected'
    try:
        logging.getLogger('fortilens.management').error(
            'management_failure event=%s category=%s', event, category)
    except Exception:
        # A broken logging handler cannot turn a handled failure into a new one.
        pass


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise Problem('unsafe_path', 'A managed path is a symbolic link.')
    temporary = path.with_name('.' + path.name + '.' + secrets.token_hex(8))
    data = value if isinstance(value, bytes) else (json.dumps(value, indent=2) + '\n').encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                # Windows denies rename while another thread briefly reads the
                # destination. Linux uses the ordinary single atomic rename.
                if os.name!='nt' or attempt==7:raise
                time.sleep(.01*(attempt+1))
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError as exc:report_failure('atomic_cleanup', exc)


def load(path, default=None):
    p = Path(path)
    return json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else default


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def guarded(root, relative):
    root = Path(root).resolve()
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise Problem('unsafe_path', 'Only registered installation files are supported.')
    target = root / relative
    if not target.resolve().is_relative_to(root) or any(p.is_symlink() for p in (target, *target.parents) if p != root.parent and p.is_relative_to(root)):
        raise Problem('unsafe_path', 'Managed files must stay inside the registered installation.')
    return target
