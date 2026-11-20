
from datetime import datetime
from pathlib import Path

from pyvcs.index import read_index
from pyvcs.objects import hash_object


def init(repo: str | Path = ".") -> None:
    """Initialize an empty Git repository."""

    repo = Path(repo).resolve()
    git_dir = repo / ".git"

    (git_dir / "objects").mkdir(parents=True, exist_ok=True)
    (git_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)

    (git_dir / "HEAD").write_text(
        "ref: refs/heads/master\n",
        encoding="ascii",
    )

    print(f"Initialized empty repository: {repo}")


def get_local_master_hash(repo: str | Path = ".") -> str | None:
    """Return the current master commit hash."""

    master_path = (
        Path(repo).resolve()
        / ".git"
        / "refs"
        / "heads"
        / "master"
    )

    if not master_path.exists():
        return None

    return master_path.read_text(encoding="ascii").strip() or None


def write_tree(repo: str | Path = ".") -> str:
    """Create a tree object from the current index."""

    repo = Path(repo).resolve()
    entries = read_index(repo)

    root = {}

    for entry in entries:
        parts = Path(entry.path).parts

        if not parts or entry.path.startswith("/"):
            raise ValueError(f"Invalid index path: {entry.path}")

        current = root

        for part in parts[:-1]:
            current = current.setdefault(part, {})

        current[parts[-1]] = entry

    def create_tree(directory: dict) -> str:
        tree_entries = []

        for name, value in directory.items():
            name_bytes = name.encode("utf-8")

            if isinstance(value, dict):
                child_sha = create_tree(value)
                mode = b"40000"
                sort_name = name_bytes + b"/"
            else:
                child_sha = value.sha1.hex()
                mode = f"{value.mode:o}".encode("ascii")
                sort_name = name_bytes

            if len(child_sha) != 40:
                raise ValueError(f"Invalid object hash: {child_sha}")

            tree_entries.append(
                (sort_name, mode + b" " + name_bytes + b"\0"
                 + bytes.fromhex(child_sha))
            )

        tree_entries.sort(key=lambda item: item[0])

        tree_data = b"".join(
            data for _, data in tree_entries
        )

        return hash_object(tree_data, "tree", repo=repo)

    return create_tree(root)


def commit(
    message: str,
    author: str,
    repo: str | Path = ".",
) -> str:
    """Create a commit from the current index."""

    repo = Path(repo).resolve()
    git_dir = repo / ".git"

    tree = write_tree(repo)
    parent = get_local_master_hash(repo)

    now = datetime.now().astimezone()
    timestamp = f"{int(now.timestamp())} {now.strftime('%z')}"

    lines = [f"tree {tree}"]

    if parent:
        lines.append(f"parent {parent}")

    lines.extend([
        f"author {author} {timestamp}",
        f"committer {author} {timestamp}",
        "",
        message,
        "",
    ])

    data = "\n".join(lines).encode("utf-8")
    sha1 = hash_object(data, "commit", repo=repo)

    master_path = git_dir / "refs" / "heads" / "master"
    master_path.parent.mkdir(parents=True, exist_ok=True)
    master_path.write_text(sha1 + "\n", encoding="ascii")

    print(f"Committed to master: {sha1[:7]}")

    return sha1
