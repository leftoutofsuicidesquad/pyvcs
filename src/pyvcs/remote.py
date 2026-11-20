
"""Remote communication and Git smart HTTP transport."""

import hashlib
import stat
import struct
import zlib

from enum import IntEnum

import requests

from pygit.objects import read_object, read_tree
from pygit.repository import get_local_master_hash


# Git pack object type identifiers.
class ObjectType(IntEnum):
    COMMIT = 1
    TREE = 2
    BLOB = 3
    TAG = 4


OBJECT_TYPES = {
    "commit": ObjectType.COMMIT,
    "tree": ObjectType.TREE,
    "blob": ObjectType.BLOB,
    "tag": ObjectType.TAG,
}

HASH_LENGTH = 40
PACK_VERSION = 2
HTTP_TIMEOUT = (10, 60)


def extract_lines(data: bytes) -> list[bytes]:
    """Decode Git pkt-line data."""

    lines = []
    offset = 0

    while offset < len(data):
        if offset + 4 > len(data):
            raise ValueError("Incomplete pkt-line header")

        try:
            length = int(data[offset:offset + 4], 16)
        except ValueError as exc:
            raise ValueError("Invalid pkt-line length") from exc

        offset += 4

        if length == 0:
            lines.append(b"")
            continue

        if length < 4:
            raise ValueError(f"Invalid pkt-line length: {length}")

        end = offset + length - 4

        if end > len(data):
            raise ValueError("Incomplete pkt-line payload")

        lines.append(data[offset:end])
        offset = end

    return lines


def build_lines_data(lines: list[bytes]) -> bytes:
    """Encode payloads as Git pkt-lines followed by flush packet."""

    result = []

    for line in lines:
        if isinstance(line, str):
            line = line.encode("utf-8")

        if not isinstance(line, bytes):
            raise TypeError("pkt-line payload must be bytes or str")

        if not line.endswith(b"\n"):
            line += b"\n"

        length = len(line) + 4

        if length > 0xFFFF:
            raise ValueError("pkt-line exceeds maximum size")

        result.append(f"{length:04x}".encode("ascii"))
        result.append(line)

    result.append(b"0000")

    return b"".join(result)


def http_request(url, username, password, data=None):
    """Perform authenticated Git HTTP request."""

    headers = {
        "User-Agent": "pygit/0.1",
    }

    if data is None:
        headers["Git-Protocol"] = "version=0"

    else:
        headers["Content-Type"] = "application/x-git-receive-pack-request"
        headers["Accept"] = "application/x-git-receive-pack-result"

    response = requests.request(
        method="POST" if data is not None else "GET",
        url=url,
        auth=(username, password),
        headers=headers,
        data=data,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.content


def get_remote_master_hash(git_url, username, password):
    """Return the remote master SHA-1 or None."""

    url = f"{git_url.rstrip('/')}/info/refs?service=git-receive-pack"

    response = http_request(url, username, password)
    lines = extract_lines(response)

    if len(lines) < 2:
        raise ValueError("Invalid receive-pack advertisement")

    if lines[0] != b"# service=git-receive-pack\n":
        raise ValueError("Invalid Git service announcement")

    if lines[1] != b"":
        raise ValueError("Missing service announcement flush packet")

    for line in lines[2:]:
        if not line:
            continue

        ref_data = line.split(b"\0", 1)[0]
        parts = ref_data.split()

        if len(parts) != 2:
            continue

        sha1, ref = parts

        if ref == b"refs/heads/master":
            if sha1 == b"0" * HASH_LENGTH:
                return None

            if len(sha1) != HASH_LENGTH:
                raise ValueError("Invalid remote SHA-1 length")

            return sha1.decode("ascii")

    return None


def find_tree_objects(tree_sha1):
    """Find all objects referenced by a tree recursively."""

    objects = set()
    pending = [tree_sha1]

    while pending:
        current = pending.pop()

        if current in objects:
            continue

        objects.add(current)

        obj_type, _ = read_object(current)

        if obj_type != "tree":
            raise ValueError(f"Expected tree object: {current}")

        for mode, path, sha1 in read_tree(sha1=current):
            if stat.S_ISDIR(mode):
                pending.append(sha1)
            else:
                objects.add(sha1)

    return objects


def find_commit_objects(commit_sha1):
    """Find commit and all recursively referenced objects."""

    objects = set()
    pending = [commit_sha1]

    while pending:
        current = pending.pop()

        if current in objects:
            continue

        objects.add(current)

        obj_type, data = read_object(current)

        if obj_type != "commit":
            raise ValueError(f"Expected commit object: {current}")

        lines = data.decode("utf-8").splitlines()

        tree_line = next(
            (line for line in lines if line.startswith("tree ")),
            None,
        )

        if tree_line is None:
            raise ValueError(f"Commit has no tree: {current}")

        tree = tree_line[5:45]
        objects.update(find_tree_objects(tree))

        for line in lines:
            if line.startswith("parent "):
                pending.append(line[7:47])

    return objects


def find_missing_objects(local_sha1, remote_sha1):
    """Find objects available locally but missing remotely."""

    if local_sha1 is None:
        return set()

    local_objects = find_commit_objects(local_sha1)

    if remote_sha1 is None:
        return local_objects

    remote_objects = find_commit_objects(remote_sha1)

    return local_objects - remote_objects


def encode_pack_object(object_sha1):
    """Encode one Git object for pack file."""

    obj_type, data = read_object(object_sha1)

    try:
        type_num = OBJECT_TYPES[obj_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported object type: {obj_type}") from exc

    size = len(data)

    # First byte: type (4 bits) + first 4 size bits.
    byte = (type_num << 4) | (size & 0x0F)
    size >>= 4

    header = bytearray()

    while size:
        header.append(byte | 0x80)
        byte = size & 0x7F
        size >>= 7

    header.append(byte)

    return bytes(header) + zlib.compress(data)


def create_pack(objects):
    """Create complete Git pack file."""

    objects = sorted(objects)

    header = struct.pack(
        "!4sLL",
        b"PACK",
        PACK_VERSION,
        len(objects),
    )

    body = b"".join(
        encode_pack_object(object_sha1)
        for object_sha1 in objects
    )

    contents = header + body
    checksum = hashlib.sha1(contents).digest()

    return contents + checksum


def push(git_url, username, password):
    """Push the local master branch to remote repository."""

    remote_sha1 = get_remote_master_hash(
        git_url, username, password
    )

    local_sha1 = get_local_master_hash()

    if local_sha1 is None:
        raise ValueError("Cannot push: no local commits")

    missing = find_missing_objects(local_sha1, remote_sha1)

    old_sha1 = remote_sha1 or ("0" * HASH_LENGTH)

    command = (
        f"{old_sha1} {local_sha1} refs/heads/master"
        "\0report-status"
    ).encode("ascii")

    request_data = build_lines_data([command]) + create_pack(missing)

    url = f"{git_url.rstrip('/')}/git-receive-pack"

    response = http_request(
        url,
        username,
        password,
        data=request_data,
    )

    lines = extract_lines(response)

    if not lines or lines[0] != b"unpack ok\n":
        raise RuntimeError(
            f"Remote rejected pack: {lines[:1]}"
        )

    status = lines[1:]

    for line in status:
        if line.startswith(b"ng "):
            raise RuntimeError(
                f"Remote rejected ref update: {line.decode(errors='replace')}"
            )

    if not any(
        line.startswith(b"ok refs/heads/master")
        for line in status
    ):
        raise RuntimeError("Remote did not confirm master update")

    print(f"Pushed to remote: {local_sha1[:7]}")

    return local_sha1
