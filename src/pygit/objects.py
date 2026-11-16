
import hashlib
import zlib
from pathlib import Path


def hash_object(data: bytes, obj_type: str, write: bool = True) -> str:
    """Calculate SHA-1 hash of a Git object and optionally store it."""

    header = f"{obj_type} {len(data)}".encode()
    full_data = header + b"\x00" + data

    sha1 = hashlib.sha1(full_data).hexdigest()

    if write:
        object_path = Path(".git") / "objects" / sha1[:2] / sha1[2:]

        if not object_path.exists():
            object_path.parent.mkdir(parents=True, exist_ok=True)
            object_path.write_bytes(zlib.compress(full_data))

    return sha1


def read_object(sha1: str):
    """Read and decompress a Git object from the object store."""

    object_path = Path(".git") / "objects" / sha1[:2] / sha1[2:]

    compressed_data = object_path.read_bytes()
    full_data = zlib.decompress(compressed_data)

    header, data = full_data.split(b"\x00", 1)

    obj_type, size = header.decode().split(" ", 1)

    if len(data) != int(size):
        raise ValueError("Corrupted Git object: size mismatch")

    return obj_type, data

