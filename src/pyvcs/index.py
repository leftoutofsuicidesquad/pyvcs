
import hashlib
import struct

from dataclasses import dataclass
from pathlib import Path


# Binary structures
INDEX_HEADER = struct.Struct("!4sLL")
INDEX_ENTRY = struct.Struct("!LLLLLLLLLL20sH")

INDEX_SIGNATURE = b"DIRC"
INDEX_VERSION = 2
CHECKSUM_SIZE = 20


class IndexError(Exception):
    """Raised when Git index is invalid or corrupted."""


@dataclass
class IndexEntry:
    """Represents single entry in the Git index."""

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


def read_index(git_dir: Path) -> list[IndexEntry]:
    """Read Git index and return entries."""

    index_path = git_dir / "index"

    if not index_path.exists():
        return []

    data = index_path.read_bytes()

    if len(data) < INDEX_HEADER.size + CHECKSUM_SIZE:
        raise IndexError("Index file is too small")

    # Verify checksum
    content = data[:-CHECKSUM_SIZE]
    checksum = data[-CHECKSUM_SIZE:]

    expected_checksum = hashlib.sha1(content).digest()

    if expected_checksum != checksum:
        raise IndexError("Invalid index checksum")

    # Read header
    signature, version, num_entries = INDEX_HEADER.unpack_from(data)

    if signature != INDEX_SIGNATURE:
        raise IndexError("Invalid index signature")

    if version != INDEX_VERSION:
        raise IndexError(f"Unsupported index version: {version}")

    # Parse entries
    entries = []
    offset = INDEX_HEADER.size
    entries_end = len(data) - CHECKSUM_SIZE

    for _ in range(num_entries):
        if offset + INDEX_ENTRY.size > entries_end:
            raise IndexError("Unexpected end of index file")

        fields = INDEX_ENTRY.unpack_from(data, offset)

        path_start = offset + INDEX_ENTRY.size

        try:
            path_end = data.index(b"\x00", path_start, entries_end)
        except ValueError:
            raise IndexError("Missing null terminator in index entry")

        path_bytes = data[path_start:path_end]

        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise IndexError("Invalid path encoding") from exc

        entries.append(
            IndexEntry(
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
        )

        # Entries padded to an 8-byte boundary
        entry_size = INDEX_ENTRY.size + len(path_bytes) + 1
        entry_size = (entry_size + 7) & ~7

        offset += entry_size

    if offset != entries_end:
        raise IndexError("Unexpected trailing data in index")

    return entries

