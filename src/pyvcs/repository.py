
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



def get_current_branch(repo="."):
    """Return the name of the currently checked-out branch."""

    repo = Path(repo).resolve()
    head_path = repo / ".git" / "HEAD"

    if not head_path.is_file():
        raise FileNotFoundError(f"Git HEAD not found: {head_path}")

    head = head_path.read_text(encoding="ascii").strip()

    prefix = "ref: refs/heads/"

    if not head.startswith(prefix):
        raise ValueError("Detached HEAD is not supported yet")

    branch = head[len(prefix):]

    if not branch or ".." in branch or branch.startswith("/"):
        raise ValueError("Invalid branch reference")

    return branch


def get_local_master_hash(repo="."):
    """Return the current branch commit hash, or None."""

    repo = Path(repo).resolve()
    branch = get_current_branch(repo)

    ref_path = repo / ".git" / "refs" / "heads" / branch

    if not ref_path.is_file():
        return None

    sha1 = ref_path.read_text(encoding="ascii").strip()

    return sha1 or None



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
