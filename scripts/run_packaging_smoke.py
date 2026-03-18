#!/usr/bin/env python3
"""Build distribution artifacts and smoke-test installed packages."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parent.parent
UV_ENV = "OPENREVIEW_SCRAPER_UV"
MIGRATIONS_DIR = ROOT / "src" / "openreview_scraper" / "migrations"
DIST_DIR_NAME = "dist"
BUILD_DIR_NAME = "build"


class PackagingSmokeError(RuntimeError):
    """Raised when a packaging validation step fails."""


def _uv_binary() -> str:
    explicit = os.environ.get(UV_ENV, "").strip()
    if explicit:
        return explicit

    uv = shutil.which("uv")
    if uv is None:
        raise PackagingSmokeError(
            "uv is required for packaging smoke validation. Install uv and retry."
        )
    return uv


def _run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    print(f"$ {' '.join(command)}")
    subprocess.run(command, cwd=cwd, env=env, check=True)


def _build_artifacts(dist_dir: Path) -> list[Path]:
    dist_dir.mkdir(parents=True, exist_ok=True)
    command = [
        _uv_binary(),
        "run",
        "--with",
        "build",
        "--with",
        "setuptools",
        "--with",
        "wheel",
        "--with",
        "twine",
        "python",
        "-m",
        "build",
        "--no-isolation",
        "--sdist",
        "--wheel",
        "-o",
        str(dist_dir),
    ]
    _run(command)
    artifacts = sorted(
        path
        for path in dist_dir.iterdir()
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    )
    if not artifacts:
        raise PackagingSmokeError(f"no artifacts were built in {dist_dir}")
    return artifacts


def _check_artifact_metadata(artifacts: list[Path]) -> None:
    command = [_uv_binary(), "run", "--with", "twine", "python", "-m", "twine", "check"]
    command.extend(str(path) for path in artifacts)
    _run(command)


def _artifact_members(artifact: Path) -> list[str]:
    if artifact.suffix == ".whl":
        with zipfile.ZipFile(artifact) as archive:
            return archive.namelist()

    if artifact.name.endswith(".tar.gz") or artifact.suffix == ".tar":
        with tarfile.open(artifact, mode="r:*") as archive:
            return archive.getnames()

    raise PackagingSmokeError(f"unsupported artifact type: {artifact}")


def _required_migration_names() -> list[str]:
    if not MIGRATIONS_DIR.exists():
        raise PackagingSmokeError(f"missing migrations directory: {MIGRATIONS_DIR}")

    migrations = sorted(path.name for path in MIGRATIONS_DIR.glob("*.sql"))
    if not migrations:
        raise PackagingSmokeError(f"no migration files found in {MIGRATIONS_DIR}")
    return migrations


def _assert_artifact_contains_migrations(artifact: Path) -> None:
    members = _artifact_members(artifact)
    required_migrations = _required_migration_names()

    for migration in required_migrations:
        expected_suffixes = (
            f"openreview_scraper/migrations/{migration}",
            f"src/openreview_scraper/migrations/{migration}",
        )
        if not any(member.endswith(suffix) for member in members for suffix in expected_suffixes):
            raise PackagingSmokeError(
                f"{artifact.name} is missing packaged migration data: {migration}"
            )


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )


def _console_script_path(venv_dir: Path) -> Path:
    script_name = "openreview-scraper.exe" if os.name == "nt" else "openreview-scraper"
    return venv_dir / ("Scripts" if os.name == "nt" else "bin") / script_name


def _install_artifact(artifact: Path, venv_dir: Path) -> None:
    _run([_uv_binary(), "venv", str(venv_dir)])
    python = _venv_python(venv_dir)
    _run([_uv_binary(), "pip", "install", "--python", str(python), str(artifact)])


def _smoke_installed_artifact(artifact: Path, smoke_dir: Path) -> None:
    artifact_key = artifact.name.replace("/", "_")
    venv_dir = smoke_dir / f"{artifact_key}-venv"
    db_path = smoke_dir / f"{artifact_key}.db"
    papers_dir = smoke_dir / f"{artifact_key}-papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _install_artifact(artifact, venv_dir)
    entrypoint = _console_script_path(venv_dir)
    if not entrypoint.exists():
        raise PackagingSmokeError(f"missing installed console script: {entrypoint}")

    env = dict(os.environ)
    env.update(
        {
            "OPENREVIEW_SCRAPER_DB_PATH": str(db_path),
            "OPENREVIEW_SCRAPER_PAPERS_DIR": str(papers_dir),
        }
    )

    _run([str(entrypoint), "--help"], cwd=ROOT, env=env)
    _run([str(entrypoint), "--version"], cwd=ROOT, env=env)
    _run([str(entrypoint), "db", "status"], cwd=ROOT, env=env)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dist-dir",
        type=Path,
        default=None,
        help="Optional directory to write built artifacts into; defaults to a temporary directory.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.dist_dir is None:
            with tempfile.TemporaryDirectory(prefix="openreview-packaging-") as tmpdir:
                dist_dir = Path(tmpdir) / DIST_DIR_NAME
                smoke_dir = Path(tmpdir) / "smoke"
                artifacts = _build_artifacts(dist_dir)
                _check_artifact_metadata(artifacts)
                for artifact in artifacts:
                    _assert_artifact_contains_migrations(artifact)
                    _smoke_installed_artifact(artifact, smoke_dir)
        else:
            dist_dir = args.dist_dir
            smoke_dir = dist_dir.parent / "smoke"
            artifacts = _build_artifacts(dist_dir)
            _check_artifact_metadata(artifacts)
            for artifact in artifacts:
                _assert_artifact_contains_migrations(artifact)
                _smoke_installed_artifact(artifact, smoke_dir)
    except (subprocess.CalledProcessError, PackagingSmokeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Packaging smoke validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
