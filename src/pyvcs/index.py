import hashlib
import struct

from dataclasses import dataclass
from pathlib import Path


INDEX_HEADER = struct.Struct("!4sLL")
INDEX_ENTRY = struct.Struct("!LLLLLLLLLL20sH")

INDEX_SIGNATURE = b"DIRC"
INDEX_VERSION = 2
CHECKSUM_SIZE = 20


class IndexError(Exception):
    """Raised when the Git index is invalid or corrupted."""


@dataclass
class IndexEntry:
    """Represents a single Git index entry."""

    ctime_s: int
    ctime_n: int
    mtime_s: int
    mtime_n: int
    dev: int
    ino: int
    mode: int
    uid: int
    gid: int
    size: int
    sha1: bytes
    flags: int
    path: str


def read_index(repo: str | Path = ".") -> list[IndexEntry]:
    """Read the Git index from a repository."""

    git_dir = Path(repo).resolve() / ".git"
    index_path = git_dir / "index"

    if not index_path.exists():
        return []

    data = index_path.read_bytes()

    if len(data) < INDEX_HEADER.size + CHECKSUM_SIZE:
        raise IndexError("Index file is too small")

    content = data[:-CHECKSUM_SIZE]
    checksum = data[-CHECKSUM_SIZE:]

    if hashlib.sha1(content).digest() != checksum:
        raise IndexError("Invalid index checksum")

    signature, version, num_entries = INDEX_HEADER.unpack_from(data)

    if signature != INDEX_SIGNATURE:
        raise IndexError("Invalid index signature")

    if version != INDEX_VERSION:
        raise IndexError(f"Unsupported index version: {version}")

    entries = []
    offset = INDEX_HEADER.size
    entries_end = len(data) - CHECKSUM_SIZE

    for _ in range(num_entries):
        if offset + INDEX_ENTRY.size > entries_end:
            raise IndexError("Unexpected end of index file")

        fields = INDEX_ENTRY.unpack_from(data, offset)
        path_start = offset + INDEX_ENTRY.size

        try:
            path_end = data.index(b"\0", path_start, entries_end)
        except ValueError as exc:
            raise IndexError("Missing null terminator") from exc

        path_bytes = data[path_start:path_end]

        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IndexError("Invalid path encoding") from exc

        entry = IndexEntry(
            ctime_s=fields[0],
            ctime_n=fields[1],
            mtime_s=fields[2],
            mtime_n=fields[3],
            dev=fields[4],
            ino=fields[5],
            mode=fields[6],
            uid=fields[7],
            gid=fields[8],
            size=fields[9],
            sha1=fields[10],
            flags=fields[11],
            path=path,
        )

        entries.append(entry)

        entry_size = INDEX_ENTRY.size + len(path_bytes) + 1
        offset += (entry_size + 7) & ~7

    if offset != entries_end:
        raise IndexError("Unexpected trailing data in index")

    return entries
