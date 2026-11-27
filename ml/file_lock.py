"""Cross-platform file locking.

On POSIX uses ``fcntl.flock``; on Windows uses ``msvcrt.locking``.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import IO, Iterator

if sys.platform != "win32":
    import fcntl
else:
    import msvcrt

# Windows msvcrt.locking operates on a byte range.  We always lock
# byte 0 so callers can use it without caring about file size.
_WIN_LOCK_BYTES = 1


def lock_exclusive(fd_or_file: int | IO) -> None:
    """Acquire an exclusive (write) lock, blocking until available."""
    if sys.platform != "win32":
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        fcntl.flock(fileno, fcntl.LOCK_EX)
    else:
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        msvcrt.locking(fileno, msvcrt.LK_LOCK, _WIN_LOCK_BYTES)


def lock_exclusive_nonblocking(fd_or_file: int | IO) -> bool:
    """Try to acquire an exclusive lock without blocking.

    Returns ``True`` on success, ``False`` if already held elsewhere.
    """
    if sys.platform != "win32":
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    else:
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        try:
            msvcrt.locking(fileno, msvcrt.LK_NBLCK, _WIN_LOCK_BYTES)
            return True
        except (OSError, IOError):
            return False


def unlock(fd_or_file: int | IO) -> None:
    """Release a previously acquired lock."""
    if sys.platform != "win32":
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        fcntl.flock(fileno, fcntl.LOCK_UN)
    else:
        fileno = fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()
        try:
            msvcrt.locking(fileno, msvcrt.LK_UNLCK, _WIN_LOCK_BYTES)
        except OSError:
            pass
