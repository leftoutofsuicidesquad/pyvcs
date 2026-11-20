
import os
from pathlib import Path
from datetime import datetime

from pygit.objects import hash_object
from pygit.index import read_index


def init(repo):
    """Initialize an empty Git repository."""

    repo = Path(repo).resolve()
    git_dir = repo / ".git"

    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)

    (git_dir / "HEAD").write_text(
        "ref: refs/heads/master\n",
        encoding="ascii"
    )

    print(f"Initialized empty repository: {repo}")


def get_local_master_hash(repo="."):
    """Return the current master commit hash, or None."""

    master_path = Path(repo) / ".git" / "refs" / "heads" / "master"

    if not master_path.exists():
        return None

    return master_path.read_text(encoding="ascii").strip() or None


def write_tree(repo="."):
    """Create a tree object from the current index."""

    entries = read_index(repo)
    tree_entries = []

    for entry in sorted(entries, key=lambda e: e.path.encode()):
        if "/" in entry.path:
            raise NotImplementedError(
                "Nested directories are not supported yet."
            )

        if len(entry.sha1) != 20:
            raise ValueError(f"Invalid SHA-1 for {entry.path}")

        mode = f"{entry.mode:o}".encode()
        path = entry.path.encode()

        tree_entries.append(
            mode + b" " + path + b"\0" + entry.sha1
        )

    return hash_object(b"".join(tree_entries), "tree")


def commit(message, author, repo="."):
    """Create a commit from the current index."""

    repo = Path(repo).resolve()
    git_dir = repo / ".git"

    tree = write_tree(repo)
    parent = get_local_master_hash(repo)

    now = datetime.now().astimezone()
    author_time = f"{int(now.timestamp())} {now.strftime('%z')}"

    lines = [f"tree {tree}"]

    if parent:
        lines.append(f"parent {parent}")

    lines.extend([
        f"author {author} {author_time}",
        f"committer {author} {author_time}",
        "",
        message,
        "",
    ])

    data = "\n".join(lines).encode("utf-8")
    sha1 = hash_object(data, "commit")

    master_path = git_dir / "refs" / "heads" / "master"
    master_path.parent.mkdir(parents=True, exist_ok=True)

    master_path.write_text(sha1 + "\n", encoding="ascii")

    print(f"Committed to master: {sha1[:7]}")

    return sha1
