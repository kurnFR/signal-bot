"""Cross-platform file locking.

On POSIX uses ``fcntl.flock``; on Windows uses ``msvcrt.locking``.

Byte-0 convention
------------------
Every lock covers exactly one byte at offset 0, so callers never need to
think about file size or the current file position.  All helpers seek to
offset 0 before locking or unlocking.  That matters on Windows, where
``msvcrt.locking`` locks from the *current* file position: a lock file
opened in ``"a"`` mode sits at EOF, so without the seek the locked byte
would be wherever the position happened to be, not byte 0.  On POSIX
``flock`` locks the whole file regardless of position, so the seek is a
no-op there — but keeping it everywhere makes the byte-0 contract
uniform across platforms.

An empty lock file is valid: the locked region "may continue beyond the
end of the file" (Python ``msvcrt`` docs), so no file-sizing hack is
needed — the lock file never needs to contain a byte.

Contention semantics
--------------------
``lock_exclusive_nonblocking`` returns ``False`` only on genuine
contention: ``BlockingIOError`` from a non-blocking ``flock`` on POSIX,
and ``EACCES`` from ``msvcrt.locking`` on Windows.  Any other
``OSError`` (bad descriptor, invalid handle, ...) propagates — it is a
programming or environment error, not a busy lock.
"""
from __future__ import annotations

import errno
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator

if sys.platform != "win32":
    import fcntl
else:
    import msvcrt

# Windows msvcrt.locking operates on a byte range.  We always lock
# byte 0 so callers can use it without caring about file size.
_WIN_LOCK_BYTES = 1


def _fileno(fd_or_file: int | IO) -> int:
    """Return the raw file descriptor for an int fd or a file object."""
    return fd_or_file if isinstance(fd_or_file, int) else fd_or_file.fileno()


def _seek_to_lock_byte(fd_or_file: int | IO) -> None:
    """Move the position to offset 0 so the locked byte is always byte 0.

    ``msvcrt.locking`` locks from the current file position, so a file
    opened in ``"a"`` mode (position at EOF) would otherwise lock the
    wrong byte.  ``os.lseek`` for raw descriptors, ``.seek(0)`` for file
    objects.  Harmless on POSIX, where ``flock`` ignores the position.
    """
    if isinstance(fd_or_file, int):
        os.lseek(fd_or_file, 0, os.SEEK_SET)
    else:
        fd_or_file.seek(0)


def lock_exclusive(fd_or_file: int | IO) -> None:
    """Acquire an exclusive (write) lock, blocking until available."""
    _seek_to_lock_byte(fd_or_file)
    fileno = _fileno(fd_or_file)
    if sys.platform != "win32":
        fcntl.flock(fileno, fcntl.LOCK_EX)
    else:
        msvcrt.locking(fileno, msvcrt.LK_LOCK, _WIN_LOCK_BYTES)


def lock_exclusive_nonblocking(fd_or_file: int | IO) -> bool:
    """Try to acquire an exclusive lock without blocking.

    Returns ``True`` on success, ``False`` only if already held elsewhere.
    Any other ``OSError`` propagates.
    """
    _seek_to_lock_byte(fd_or_file)
    fileno = _fileno(fd_or_file)
    if sys.platform != "win32":
        try:
            fcntl.flock(fileno, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    else:
        try:
            msvcrt.locking(fileno, msvcrt.LK_NBLCK, _WIN_LOCK_BYTES)
            return True
        except OSError as exc:
            if exc.errno == errno.EACCES:
                return False
            raise


def unlock(fd_or_file: int | IO) -> None:
    """Release a previously acquired lock."""
    _seek_to_lock_byte(fd_or_file)
    fileno = _fileno(fd_or_file)
    if sys.platform != "win32":
        fcntl.flock(fileno, fcntl.LOCK_UN)
    else:
        try:
            msvcrt.locking(fileno, msvcrt.LK_UNLCK, _WIN_LOCK_BYTES)
        except OSError:
            pass


@contextmanager
def exclusive_lock(fd_or_file: int | IO) -> Iterator[None]:
    """Acquire an exclusive lock for the body of a ``with`` statement.

    Releases the lock in a ``finally`` even if the body raises.
    """
    lock_exclusive(fd_or_file)
    try:
        yield
    finally:
        unlock(fd_or_file)


def fsync_directory(directory: Path | str) -> None:
    """Persist a directory entry after an atomic ``os.replace``.

    On POSIX, opening the directory read-only and fsyncing it flushes the
    rename to disk. On Windows this is a no-op: Windows cannot open a
    directory with ``os.open`` (``PermissionError: [Errno 13]``) and does
    not support fsync on directories, so there is nothing to flush — the
    rename itself is already visible. Callers must use this helper
    instead of ``os.open(dir, os.O_RDONLY)`` + ``os.fsync`` so the same
    code runs on both platforms.
    """
    if sys.platform == "win32":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
