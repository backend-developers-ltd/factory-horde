"""Linux shared-tree I/O: no-follow paths, atomic visibility and fsync durability."""

import errno
import json
import os
import stat
import threading
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import uuid4

import structlog
from prometheus_client import Counter, Histogram
from pydantic import TypeAdapter

from validator.records import Record, RelativePath

_logger = structlog.get_logger(__name__)
_events = Counter("factory_horde_record_operations_total", "Shared-record operations", ("operation", "outcome"))
_latency = Histogram("factory_horde_record_operation_seconds", "Shared-record operation duration", ("operation",))
_path_adapter = TypeAdapter[str](RelativePath)
_directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_max_record_bytes = 1024 * 1024


class RecordConflictError(ValueError):
    """An immutable identity already has different committed content."""


class RecordFormatError(ValueError):
    """A file is not one complete, bounded, unambiguous record."""


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RecordFormatError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise RecordFormatError(f"non-finite JSON value: {value}")


def encode_record(record: Record) -> bytes:
    """Canonical UTF-8 JSON; semantic equality gives identical publication bytes."""
    validated = type(record).model_validate(record)
    content = json.dumps(validated.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return (content + "\n").encode()


def decode_record[T: Record](payload: bytes, model: type[T]) -> T:
    """Reject malformed content before applying strict versioned validation.

    Raises:
        RecordFormatError: JSON encoding, structure or numeric representation is invalid.
    """
    try:
        json.loads(payload, object_pairs_hook=_unique_keys, parse_constant=_invalid_constant)
    except RecordFormatError:
        raise
    except (ValueError, RecursionError) as error:
        raise RecordFormatError("Malformed JSON record") from error
    return model.model_validate_json(payload)


class RecordFiles:
    """Thread-safe operations under one pre-created absolute root on a local Linux filesystem.

    Every path component is opened with O_NOFOLLOW. Immutable publication uses a
    hard link for atomic create-if-absent, then unlinks its same-directory temporary
    name. File fsync precedes publication; directory fsync precedes success. Mutable
    observations use atomic replace. OS errors propagate; failed durability never
    authorizes dispatch. No cross-host/NFS durability guarantee is assumed.
    """

    def __init__(self, root: Path) -> None:
        if not root.is_absolute() or ".." in root.parts:
            raise ValueError("data root must be an absolute path without traversal")
        self.root = root
        self.lock = threading.RLock()
        with self._directory((), create=False):
            pass

    @contextmanager
    def _operation(self, operation: Literal["read", "publish", "replace", "mkdir", "list"]) -> Generator[None]:
        with self.lock, _latency.labels(operation).time():
            try:
                yield
            except FileNotFoundError:
                _events.labels(operation, "missing").inc()
                raise
            except Exception:
                _events.labels(operation, "error").inc()
                _logger.exception("record_operation_failed", operation=operation)
                raise
            else:
                _events.labels(operation, "ok").inc()

    @contextmanager
    def _directory(self, parts: tuple[str, ...], *, create: bool) -> Generator[int]:
        directory = os.open("/", _directory_flags)
        try:
            for component in self.root.parts[1:]:
                child = os.open(component, _directory_flags, dir_fd=directory)
                os.close(directory)
                directory = child
            for component in parts:
                if create:
                    try:
                        os.mkdir(component, mode=0o750, dir_fd=directory)
                    except FileExistsError:
                        pass
                    # Also sync on replay: a previous mkdir may have survived a failed fsync.
                    os.fsync(directory)
                child = os.open(component, _directory_flags, dir_fd=directory)
                os.close(directory)
                directory = child
            yield directory
        finally:
            os.close(directory)

    @staticmethod
    def _parts(relative: str) -> tuple[str, ...]:
        return tuple(_path_adapter.validate_python(relative).split("/"))

    @staticmethod
    def _read(directory: int, name: str) -> bytes:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise RecordFormatError("record must be a regular file")
            payload = source.read(_max_record_bytes + 1)
            if len(payload) > _max_record_bytes:
                raise RecordFormatError("record exceeds the one-MiB file limit")
            return payload

    def read_bytes(self, relative: str) -> bytes:
        """Read one regular file without following any symbolic links."""
        parts = self._parts(relative)
        with self._operation("read"), self._directory(parts[:-1], create=False) as directory:
            return self._read(directory, parts[-1])

    def read_artifact(self, relative: str) -> bytes:
        """Read untrusted output, distinguishing invalid paths/permissions from transient host I/O.

        Raises:
            RecordFormatError: The workload supplied an unsafe or unreadable artifact.
            OSError: Missing output or a transient host I/O error remains distinguishable.
        """
        try:
            return self.read_bytes(relative)
        except OSError as error:
            if error.errno in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR, errno.EACCES, errno.EPERM):
                raise RecordFormatError("Artifact path or permissions are invalid") from error
            raise

    def read[T: Record](self, relative: str, model: type[T]) -> T:
        """Parse one complete record; malformed final files are errors, not observations."""
        return decode_record(self.read_bytes(relative), model)

    def mkdir(self, relative: str) -> None:
        """Create and durably publish owned directories; reject existing symlinks."""
        with self._operation("mkdir"), self._directory(self._parts(relative), create=True) as directory:
            os.fsync(directory)

    def list_names(self, relative: str) -> tuple[str, ...]:
        """List one directory; callers select their exact final-record names."""
        with self._operation("list"), self._directory(self._parts(relative), create=False) as directory:
            return tuple(sorted(os.listdir(directory)))

    def publish(self, relative: str, record: Record) -> None:
        """Publish immutable JSON idempotently; conflicting identity reuse fails."""
        self.write_bytes(relative, encode_record(record), immutable=True)

    def replace(self, relative: str, record: Record) -> None:
        """Replace a current observation atomically; record ownership is the caller's contract."""
        self.write_bytes(relative, encode_record(record), immutable=False)

    def write_bytes(self, relative: str, payload: bytes, *, immutable: bool) -> None:
        """Publish complete bytes and fsync before returning to a side-effecting caller.

        Raises:
            RecordFormatError: An oversized record or non-file replacement target.
            RecordConflictError: Existing immutable content differs.
        """
        if len(payload) > _max_record_bytes:
            raise RecordFormatError("record exceeds the one-MiB file limit")
        parts = self._parts(relative)
        operation = "publish" if immutable else "replace"
        with self._operation(operation), self._directory(parts[:-1], create=True) as directory:
            target = parts[-1]
            temporary = f".{target}.{uuid4()}.tmp"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o640, dir_fd=directory)
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(payload)
                    output.flush()
                    os.fsync(output.fileno())
                if immutable:
                    try:
                        os.link(temporary, target, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
                    except FileExistsError:
                        if self._read(directory, target) != payload:
                            raise RecordConflictError(f"conflicting immutable publication: {relative}") from None
                else:
                    try:
                        existing = os.stat(target, dir_fd=directory, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        if not stat.S_ISREG(existing.st_mode):
                            raise RecordFormatError("replacement target must be a regular file")
                    os.replace(temporary, target, src_dir_fd=directory, dst_dir_fd=directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.fsync(directory)
