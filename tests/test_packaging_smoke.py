"""Tests for packaging build and smoke helpers."""

from __future__ import annotations

import io
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import ANY, call, patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import run_packaging_smoke


class PackagingSmokeTests(unittest.TestCase):
    def _write_wheel(self, path: Path, *, include_all_migrations: bool = True) -> None:
        migrations = run_packaging_smoke._required_migration_names()
        if not include_all_migrations:
            migrations = migrations[:-1]

        with zipfile.ZipFile(path, mode="w") as archive:
            archive.writestr("openreview_scraper/__init__.py", "")
            for migration in migrations:
                archive.writestr(f"openreview_scraper/migrations/{migration}", "-- sql\n")

    def _write_sdist(self, path: Path, *, include_all_migrations: bool = True) -> None:
        migrations = run_packaging_smoke._required_migration_names()
        if not include_all_migrations:
            migrations = migrations[:-1]

        with tarfile.open(path, mode="w:gz") as archive:
            root = "openreview_scraper-0.1.0"
            init_info = tarfile.TarInfo(f"{root}/src/openreview_scraper/__init__.py")
            init_bytes = b""
            init_info.size = len(init_bytes)
            archive.addfile(init_info, io.BytesIO(init_bytes))

            for migration in migrations:
                payload = b"-- sql\n"
                info = tarfile.TarInfo(
                    f"{root}/src/openreview_scraper/migrations/{migration}"
                )
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))

    def test_build_artifacts_uses_no_isolation_build_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dist_dir = Path(tmpdir) / "dist"

            def fake_run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
                dist_dir.mkdir(parents=True, exist_ok=True)
                (dist_dir / "openreview_scraper-0.1.0.tar.gz").write_text("sdist", encoding="utf-8")
                (dist_dir / "openreview_scraper-0.1.0-py3-none-any.whl").write_text("wheel", encoding="utf-8")

            with patch.object(run_packaging_smoke, "_uv_binary", return_value="/tmp/uv"):
                with patch.object(run_packaging_smoke, "_run", side_effect=fake_run) as run:
                    artifacts = run_packaging_smoke._build_artifacts(dist_dir)

        self.assertEqual(
            run.call_args.args[0],
            [
                "/tmp/uv",
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
            ],
        )
        self.assertEqual({path.name for path in artifacts}, {
            "openreview_scraper-0.1.0.tar.gz",
            "openreview_scraper-0.1.0-py3-none-any.whl",
        })

    def test_assert_artifact_contains_migrations_accepts_wheel_and_sdist_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            wheel = tmp / "openreview_scraper-0.1.0-py3-none-any.whl"
            sdist = tmp / "openreview_scraper-0.1.0.tar.gz"
            self._write_wheel(wheel)
            self._write_sdist(sdist)

            run_packaging_smoke._assert_artifact_contains_migrations(wheel)
            run_packaging_smoke._assert_artifact_contains_migrations(sdist)

    def test_assert_artifact_contains_migrations_rejects_missing_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = Path(tmpdir) / "openreview_scraper-0.1.0-py3-none-any.whl"
            self._write_wheel(artifact, include_all_migrations=False)

            with self.assertRaisesRegex(
                run_packaging_smoke.PackagingSmokeError,
                "missing packaged migration data",
            ):
                run_packaging_smoke._assert_artifact_contains_migrations(artifact)

    def test_install_and_smoke_commands_use_isolated_env_and_console_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            artifact = tmp / "openreview_scraper-0.1.0-py3-none-any.whl"
            artifact.write_text("artifact", encoding="utf-8")
            smoke_dir = tmp / "smoke"
            venv_dir = smoke_dir / f"{artifact.name}-venv"
            script_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
            script_dir.mkdir(parents=True)
            console_script = script_dir / (
                "openreview-scraper.exe" if sys.platform == "win32" else "openreview-scraper"
            )
            console_script.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

            with patch.object(run_packaging_smoke, "_uv_binary", return_value="/tmp/uv"):
                with patch.object(run_packaging_smoke, "_install_artifact") as install_artifact:
                    install_artifact.side_effect = lambda artifact, venv_dir: None
                    with patch.object(run_packaging_smoke, "_run") as run:
                        run_packaging_smoke._smoke_installed_artifact(artifact, smoke_dir)

        expected_script = str(console_script)
        self.assertEqual(
            install_artifact.call_args.args,
            (artifact, venv_dir),
        )
        self.assertEqual(
            run.call_args_list,
            [
                call([expected_script, "--help"], cwd=ROOT, env=ANY),
                call([expected_script, "--version"], cwd=ROOT, env=ANY),
                call([expected_script, "db", "status"], cwd=ROOT, env=ANY),
            ],
        )
        first_env = run.call_args_list[0].kwargs["env"]
        self.assertEqual(first_env["OPENREVIEW_SCRAPER_DB_PATH"], str(smoke_dir / f"{artifact.name}.db"))
        self.assertEqual(
            first_env["OPENREVIEW_SCRAPER_PAPERS_DIR"],
            str(smoke_dir / f"{artifact.name}-papers"),
        )


if __name__ == "__main__":
    unittest.main()
