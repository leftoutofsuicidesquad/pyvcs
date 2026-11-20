
import hashlib
import zlib

from pathlib import Path


def get_git_dir(repo: str | Path = ".") -> Path:
    """Return the Git directory of a repository."""

    repo = Path(repo).resolve()
    git_dir = repo if repo.name == ".git" else repo / ".git"

    if not git_dir.is_dir():
        raise FileNotFoundError(f"Git repository not found: {repo}")

    return git_dir


def hash_object(
    data: bytes,
    obj_type: str,
    write: bool = True,
    repo: str | Path = ".",
) -> str:
    """Calculate the SHA-1 hash of a Git object and optionally store it."""

    if obj_type not in {"blob", "tree", "commit", "tag"}:
        raise ValueError(f"Invalid Git object type: {obj_type}")

    header = f"{obj_type} {len(data)}".encode("ascii")
    full_data = header + b"\0" + data

    sha1 = hashlib.sha1(full_data).hexdigest()

    if write:
        git_dir = get_git_dir(repo)
        object_path = git_dir / "objects" / sha1[:2] / sha1[2:]

        if not object_path.exists():
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_bytes(zlib.compress(full_data))

    return sha1


def read_object(
    sha1: str,
    repo: str | Path = ".",
) -> tuple[str, bytes]:
    """Read and decompress a Git object."""

    if len(sha1) != 40:
        raise ValueError("Invalid SHA-1 length")

    git_dir = get_git_dir(repo)
    object_path = git_dir / "objects" / sha1[:2] / sha1[2:]

    full_data = zlib.decompress(object_path.read_bytes())

    try:
        header, data = full_data.split(b"\0", 1)
        obj_type, size = header.decode("ascii").split(" ", 1)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid Git object") from exc

    if len(data) != int(size):
        raise ValueError("Corrupted Git object: size mismatch")

    return obj_type, data
