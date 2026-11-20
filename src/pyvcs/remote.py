
"""Remote communication and Git smart HTTP transport."""

import hashlib
import struct
import zlib

from enum import IntEnum
from pathlib import Path

import requests

from pyvcs.objects import read_object
from pyvcs.repository import get_local_master_hash

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
            raise ValueError(f"Unsupported pkt-line marker: {length}")

        end = offset + length - 4

        if end > len(data):
            raise ValueError("Incomplete pkt-line payload")

        lines.append(data[offset:end])
        offset = end

    return lines


def build_lines_data(lines: list[bytes | str]) -> bytes:
    """Encode payloads as Git pkt-lines followed by a flush packet."""

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


def http_request(url, username=None, password=None, data=None):
    """Perform an authenticated Git smart HTTP request."""

    is_post = data is not None

    headers = {
        "User-Agent": "pyvcs/0.1",
    }

    if is_post:
        headers.update({
            "Content-Type": "application/x-git-receive-pack-request",
            "Accept": "application/x-git-receive-pack-result",
        })
    else:
        if "service=git-receive-pack" in url:
            headers["Accept"] = "application/x-git-receive-pack-advertisement"
        else:
            headers["Accept"] = "application/x-git-upload-pack-advertisement"

    auth = (username, password) if username is not None else None

    response = requests.request(
        method="POST" if is_post else "GET",
        url=url,
        auth=auth,
        headers=headers,
        data=data,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()
    return response.content


def get_remote_branch_hash(git_url, username=None, password=None, branch="master"):
    """Return the remote master SHA-1 or None."""

    url = f"{git_url.rstrip('/')}/info/refs?service=git-upload-pack"
    response = http_request(url, username, password)
    lines = extract_lines(response)

    if len(lines) < 2:
        raise ValueError("Invalid receive-pack advertisement")

    if lines[0] != b"# service=git-upload-pack\n":
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

        if ref == f"refs/heads/{branch}".encode("ascii"):
            if sha1 == b"0" * HASH_LENGTH:
                return None

            if len(sha1) != HASH_LENGTH:
                raise ValueError("Invalid remote SHA-1 length")

            return sha1.decode("ascii")

    return None


def read_tree_entries(tree_sha1, repo="."):
    """Parse a Git tree object into (mode, name, SHA-1) entries."""

    obj_type, data = read_object(tree_sha1, repo=repo)

    if obj_type != "tree":
        raise ValueError(f"Expected tree object: {tree_sha1}")

    entries = []
    offset = 0

    while offset < len(data):
        space = data.find(b" ", offset)
        if space == -1:
            raise ValueError("Invalid tree entry: missing mode separator")

        null = data.find(b"\0", space + 1)
        if null == -1:
            raise ValueError("Invalid tree entry: missing name terminator")

        sha_start = null + 1
        sha_end = sha_start + 20

        if sha_end > len(data):
            raise ValueError("Invalid tree entry: incomplete object ID")

        try:
            mode = int(data[offset:space], 8)
        except ValueError as exc:
            raise ValueError("Invalid tree entry mode") from exc

        name = data[space + 1:null]
        sha1 = data[sha_start:sha_end].hex()

        entries.append((mode, name, sha1))
        offset = sha_end

    return entries


def find_tree_objects(tree_sha1, repo="."):
    """Find all objects referenced by a tree recursively."""

    objects = set()
    pending = [tree_sha1]

    while pending:
        current = pending.pop()

        if current in objects:
            continue

        objects.add(current)

        for mode, _name, sha1 in read_tree_entries(current, repo):
            # Directories are tree objects.
            if mode == 0o40000:
                pending.append(sha1)

            # Gitlinks (submodules) refer to commits in another repository.
            elif mode == 0o160000:
                continue

            else:
                objects.add(sha1)

    return objects


def find_commit_objects(commit_sha1, repo="."):
    """Find a commit and all recursively referenced local objects."""

    objects = set()
    pending = [commit_sha1]

    while pending:
        current = pending.pop()

        if current in objects:
            continue

        obj_type, data = read_object(current, repo=repo)

        if obj_type != "commit":
            raise ValueError(f"Expected commit object: {current}")

        objects.add(current)

        lines = data.decode("utf-8").splitlines()

        tree_line = next(
            (line for line in lines if line.startswith("tree ")),
            None,
        )

        if tree_line is None:
            raise ValueError(f"Commit has no tree: {current}")

        tree_sha1 = tree_line[5:45]
        objects.update(find_tree_objects(tree_sha1, repo))

        for line in lines:
            if line.startswith("parent "):
                pending.append(line[7:47])

    return objects


def encode_pack_object(object_sha1, repo="."):
    """Encode one Git object for a pack file."""

    obj_type, data = read_object(object_sha1, repo=repo)

    try:
        type_num = OBJECT_TYPES[obj_type]
    except KeyError as exc:
        raise ValueError(f"Unsupported object type: {obj_type}") from exc

    size = len(data)

    first_byte = (int(type_num) << 4) | (size & 0x0F)
    size >>= 4

    header = bytearray()

    if size:
        first_byte |= 0x80

    header.append(first_byte)

    while size:
        byte = size & 0x7F
        size >>= 7

        if size:
            byte |= 0x80

        header.append(byte)

    return bytes(header) + zlib.compress(data)


def create_pack(objects, repo="."):
    """Create a complete, non-delta Git pack file."""

    objects = sorted(objects)

    header = struct.pack(
        "!4sLL",
        b"PACK",
        PACK_VERSION,
        len(objects),
    )

    body = b"".join(
        encode_pack_object(object_sha1, repo)
        for object_sha1 in objects
    )

    contents = header + body
    checksum = hashlib.sha1(contents).digest()

    return contents + checksum



def push(git_url, username=None, password=None, repo="."):
    """Push the current local branch to a remote repository."""

    repo = Path(repo).resolve()
    branch = get_current_branch(repo)
    local_sha1 = get_local_master_hash(repo)

    if local_sha1 is None:
        raise ValueError(f"Cannot push: branch '{branch}' has no commits")

    objects = find_commit_objects(local_sha1, repo)

    remote_sha1 = get_remote_branch_hash(
        git_url,
        username,
        password,
        branch=branch,
    )

    old_sha1 = remote_sha1 or ("0" * HASH_LENGTH)
    ref = f"refs/heads/{branch}"

    command = (
        f"{old_sha1} {local_sha1} {ref}\0report-status"
    ).encode("ascii")

    request_data = (
        build_lines_data([command])
        + create_pack(objects, repo)
    )

    url = f"{git_url.rstrip('/')}/git-receive-pack"

    response = http_request(
        url,
        username,
        password,
        data=request_data,
    )

    lines = extract_lines(response)

    if not lines or lines[0] != b"unpack ok\n":
        raise RuntimeError(f"Remote rejected pack: {lines[:1]}")

    status = lines[1:]

    for line in status:
        if line.startswith(b"ng "):
            raise RuntimeError(
                "Remote rejected ref update: "
                + line.decode("utf-8", errors="replace")
            )

    expected = f"ok {ref}".encode("ascii")

    if not any(line.rstrip(b"\n") == expected for line in status):
        raise RuntimeError("Remote did not confirm branch update")

    print(f"Pushed {branch}: {local_sha1[:7]}")
    return local_sha1

