"""Tests for repo-local CLI bootstrap helpers."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import ANY, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import bootstrap_local_cli


class BootstrapLocalCLITests(unittest.TestCase):
    def test_bootstrap_reason_reports_missing_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.object(bootstrap_local_cli, "ROOT", root):
                with patch.object(bootstrap_local_cli, "VENV_DIR", root / ".venv"):
                    with patch.object(
                        bootstrap_local_cli,
                        "STAMP_PATH",
                        root / ".venv" / ".bootstrap-stamp",
                    ):
                        reason = bootstrap_local_cli.bootstrap_reason("openreview-scraper")

        self.assertEqual(reason, "missing-venv")

    def test_bootstrap_reason_reports_stale_bootstrap_when_pyproject_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_dir = root / ".venv"
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            stamp_path = venv_dir / ".bootstrap-stamp"
            pyproject = root / "pyproject.toml"

            (bin_dir / "python").write_text("", encoding="utf-8")
            stamp_path.write_text("bootstrapped\n", encoding="utf-8")
            pyproject.write_text("[project]\nname='demo'\n", encoding="utf-8")

            os.utime(stamp_path, (1_000, 1_000))
            os.utime(pyproject, (1_010, 1_010))

            with patch.object(bootstrap_local_cli, "ROOT", root):
                with patch.object(bootstrap_local_cli, "VENV_DIR", venv_dir):
                    with patch.object(bootstrap_local_cli, "STAMP_PATH", stamp_path):
                        with patch.object(bootstrap_local_cli, "venv_python_usable", return_value=True):
                            reason = bootstrap_local_cli.bootstrap_reason("openreview-scraper")

        self.assertEqual(reason, "stale-bootstrap:pyproject.toml")

    def test_bootstrap_reason_reports_broken_venv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_dir = root / ".venv"
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "python").write_text("", encoding="utf-8")

            with patch.object(bootstrap_local_cli, "ROOT", root):
                with patch.object(bootstrap_local_cli, "VENV_DIR", venv_dir):
                    with patch.object(
                        bootstrap_local_cli,
                        "STAMP_PATH",
                        root / ".venv" / ".bootstrap-stamp",
                    ):
                        with patch.object(bootstrap_local_cli, "venv_python_usable", return_value=False):
                            reason = bootstrap_local_cli.bootstrap_reason("openreview-scraper")

        self.assertEqual(reason, "broken-venv")

    def test_dispatch_bootstraps_then_runs_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_dir = root / ".venv"
            bin_dir = venv_dir / "bin"
            bin_dir.mkdir(parents=True)
            python = bin_dir / "python"
            python.write_text("", encoding="utf-8")

            with patch.object(bootstrap_local_cli, "ROOT", root):
                with patch.object(bootstrap_local_cli, "VENV_DIR", venv_dir):
                    with patch.object(
                        bootstrap_local_cli,
                        "STAMP_PATH",
                        venv_dir / ".bootstrap-stamp",
                    ):
                        with patch.object(
                            bootstrap_local_cli, "bootstrap_reason", return_value="missing-venv"
                        ):
                            with patch.object(bootstrap_local_cli, "run_bootstrap") as run_bootstrap:
                                with patch("scripts.bootstrap_local_cli.subprocess.run") as run:
                                    run.return_value.returncode = 0
                                    exit_code = bootstrap_local_cli.dispatch(
                                        "openreview-scraper",
                                        ["--help"],
                                    )

        run_bootstrap.assert_called_once_with("missing-venv")
        run.assert_called_once_with(
            [str(python), "-m", "openreview_scraper", "--help"],
            cwd=root,
            env=ANY,
        )
        self.assertEqual(run.call_args.kwargs["env"]["PYTHONPATH"], str(root / "src"))
        self.assertEqual(exit_code, 0)

    def test_ensure_venv_skips_recreation_for_usable_env(self) -> None:
        with patch.object(bootstrap_local_cli, "venv_python_usable", return_value=True):
            with patch("scripts.bootstrap_local_cli.subprocess.run") as run:
                bootstrap_local_cli.ensure_venv()

        run.assert_not_called()

    def test_ensure_venv_clears_existing_broken_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            venv_dir = root / ".venv"
            venv_dir.mkdir()

            with patch.object(bootstrap_local_cli, "ROOT", root):
                with patch.object(bootstrap_local_cli, "VENV_DIR", venv_dir):
                    with patch.object(bootstrap_local_cli, "venv_python_usable", return_value=False):
                        with patch("scripts.bootstrap_local_cli.shutil.rmtree") as rmtree:
                            with patch("scripts.bootstrap_local_cli.subprocess.run") as run:
                                bootstrap_local_cli.ensure_venv()

        rmtree.assert_called_once_with(venv_dir)
        run.assert_called_once_with(
            [sys.executable, "-m", "venv", str(venv_dir)],
            cwd=root,
            check=True,
        )

    def test_run_bootstrap_for_missing_stamp_only_writes_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            stamp_path = root / ".venv" / ".bootstrap-stamp"
            stamp_path.parent.mkdir(parents=True)

            with patch.object(bootstrap_local_cli, "STAMP_PATH", stamp_path):
                with patch.object(bootstrap_local_cli, "ensure_venv") as ensure_venv:
                    bootstrap_local_cli.run_bootstrap("missing-stamp")

            self.assertEqual(stamp_path.read_text(encoding="utf-8"), "bootstrapped\n")

        ensure_venv.assert_called_once_with()

    def test_bootstrap_reason_rejects_unknown_entrypoint(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported entrypoint"):
            bootstrap_local_cli.bootstrap_reason("unknown-cli")


if __name__ == "__main__":
    unittest.main()
