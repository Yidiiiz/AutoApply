"""Absolute operation budgets and local file-version evidence."""
import hashlib
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Deadline:
    expires: float

    @classmethod
    def after(cls, seconds):
        return cls(time.monotonic() + seconds)

    @property
    def remaining(self):
        return max(0, self.expires - time.monotonic())

    def milliseconds(self):
        if not self.remaining:
            raise TimeoutError('Operation deadline exhausted')
        return max(1, self.remaining * 1000)


@dataclass(frozen=True)
class FileSnapshot:
    path: str
    version: tuple
    sha256: str
    is_pdf: bool

    @staticmethod
    def identity(path):
        s = path.stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    @classmethod
    def read(cls, path, previous=None, *, force=False):
        path = Path(path).resolve()
        version = cls.identity(path)
        if not force and previous and previous.path == str(path) and previous.version == version:
            return previous
        contents = path.read_bytes()
        if cls.identity(path) != version:
            raise ValueError('Resume changed while being read')
        return cls(str(path), version, hashlib.sha256(contents).hexdigest(), contents.startswith(b'%PDF-'))
