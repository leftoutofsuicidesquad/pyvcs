import hashlib
import struct
import stat

from dataclasses import dataclass
from pathlib import Path
from pyvcs.objects import hash_object, get_git_dir

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

    while offset < entries_end:
        if offset + 8 > entries_end:
            raise IndexError("Invalid index extension")

        extension_size = struct.unpack_from("!L", data, offset + 4)[0]
        offset += 8 + extension_size

    if offset != entries_end:
        raise IndexError("Invalid index extension size")

    return entries

def write_index(entries: list[IndexEntry], repo: str | Path = ".") -> None:
    """Write the Git index to disk."""

    git_dir = get_git_dir(repo)
    index_path = git_dir / "index"

    entries.sort(key=lambda entry: entry.path)

    data = INDEX_HEADER.pack(
        INDEX_SIGNATURE,
        INDEX_VERSION,
        len(entries),
    )

    for entry in entries:
        path_bytes = entry.path.encode("utf-8")

        entry_data = INDEX_ENTRY.pack(
            entry.ctime_s,
            entry.ctime_n,
            entry.mtime_s,
            entry.mtime_n,
            entry.dev,
            entry.ino,
            entry.mode,
            entry.uid,
            entry.gid,
            entry.size,
            entry.sha1,
            entry.flags,
        )

        entry_data += path_bytes + b"\0"
        entry_data += b"\0" * ((-len(entry_data)) % 8)
        data += entry_data

    data += hashlib.sha1(data).digest()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_bytes(data)


def add(path=".", repo="."):
    """Add files to the repository index."""
    repo = Path(repo).resolve()
    target = (repo / path).resolve()
    if not target.is_relative_to(repo):
        raise ValueError("Path is outside the repository")

    excluded = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "build",
        "dist",
    }

    if target.is_file():
        files = [target]
    else:
        files = [
            file for file in target.rglob("*")
            if file.is_file()
            and not any(
                part in excluded or part.endswith(".egg-info")
                for part in file.relative_to(repo).parts
            )
        ]

    entries = read_index(repo)
    entry_map = {entry.path: entry for entry in entries}

    for file in files:
        relative_path = file.relative_to(repo).as_posix()
        data = file.read_bytes()
        sha1 = hash_object(data, "blob", repo=repo)
        stat_result = file.stat()

        entry_map[relative_path] = IndexEntry(
            ctime_s=int(stat_result.st_ctime),
            ctime_n=0,
            mtime_s=int(stat_result.st_mtime),
            mtime_n=0,
            dev=stat_result.st_dev,
            ino=stat_result.st_ino,
            mode=stat.S_IFREG | (0o755 if stat_result.st_mode & 0o111 else 0o644),
            uid=stat_result.st_uid,
            gid=stat_result.st_gid,
            size=stat_result.st_size,
            sha1=bytes.fromhex(sha1),
            flags=min(len(relative_path.encode("utf-8")), 0xFFF),
            path=relative_path,
        )

    write_index(list(entry_map.values()), repo)
    print(f"Added {len(files)} file(s)")
