"""Copies what the demo image needs into a folder, ready to push to a Hugging Face Space.

A Space is its own git repository, and its README must start with the Space's settings. So
the Space gets its own short README and only the files the image is built from. Run it with
the path of your cloned Space as the one argument, then commit and push from that folder.
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FOLDERS = ("agent", "api", "data", "evals", "mcp_server", "console", "deploy")
FILES = ("Dockerfile", ".dockerignore", ".gitattributes", "pyproject.toml", "LICENSE", "docs/EVALS.md")
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "node_modules", "dist", ".pytest_cache", "*.egg-info")


def export(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    for name in FOLDERS:
        shutil.copytree(ROOT / name, target / name, ignore=SKIP, dirs_exist_ok=True)
    for name in FILES:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target / name)
    shutil.copy2(ROOT / "deploy" / "SPACE_README.md", target / "README.md")
    return sorted(p.name for p in target.iterdir() if p.name != ".git")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Give the folder of your cloned Space, for example: python deploy/export_space.py ../deflect-space")
    print("Copied: " + ", ".join(export(Path(sys.argv[1]).resolve())))
    print("Now commit and push from that folder. The Space builds the image by itself.")
