from pathlib import Path

def init(repo):
    """Initialize empty Git repository."""

    repo = Path(repo)
    git_dir = repo / ".git"

    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs" / "heads").mkdir(parents=True)

    (git_dir / "HEAD").write_bytes(
        b"ref: refs/heads/master"
    )

    print(f"Initialized empty repository: {repo}")
