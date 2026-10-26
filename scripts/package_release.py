"""Create source and ModelScope ZIPs with checksums; never package local credentials."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="v0.1.0-demo")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/releases")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.version):
        parser.error("Version must contain only letters, digits, dots, underscores or hyphens.")
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    files = [Path(name) for name in tracked if name]
    for relative in files:
        if any(part in {".git", "node_modules", ".venv", "__pycache__", ".github-auth", ".herenow", "build"} for part in relative.parts):
            raise SystemExit(f"Unexpected tracked local file: {relative}")
        if relative.name == ".env" or (relative.name.startswith(".env.") and relative.name != ".env.example") or relative.name in {"hosts.yml", ".DS_Store"} or relative.suffix in {".pyc", ".zip"}:
            raise SystemExit(f"Unexpected tracked file: {relative}")
        if not (ROOT / relative).is_file() or (ROOT / relative).is_symlink():
            raise SystemExit(f"Tracked file missing or symlink: {relative}")
    subprocess.run([sys.executable, "scripts/build_space.py"], cwd=ROOT, check=True)
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = output / f"weekend-agent-source-{args.version}.zip"
    deployment = output / f"weekend-agent-modelscope-{args.version}.zip"
    with ZipFile(source, "w", ZIP_DEFLATED) as archive:
        for relative in files:
            archive.write(ROOT / relative, Path("weekend-local-agent") / relative)
    flat = ROOT / "build/modelscope"
    with ZipFile(deployment, "w", ZIP_DEFLATED) as archive:
        for file in sorted(flat.rglob("*")):
            if file.is_file():
                archive.write(file, file.relative_to(flat))
    hashes = []
    for file in (source, deployment):
        with ZipFile(file) as archive:
            if archive.testzip() is not None:
                raise SystemExit(f"ZIP verification failed: {file.name}")
        hashes.append(hashlib.sha256(file.read_bytes()).hexdigest() + "  " + file.name)
        print(f"{file.name}: {file.stat().st_size} bytes")
    (output / "SHA256SUMS.txt").write_text("\n".join(hashes) + "\n")
    print(f"Release files: {output}")


if __name__ == "__main__":
    main()
