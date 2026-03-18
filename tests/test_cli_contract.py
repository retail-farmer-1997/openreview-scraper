"""Tests for the CLI contract."""

from __future__ import annotations

from pathlib import Path
import sys
import types
import unittest

from click.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _install_openreview_stub() -> None:
    if "openreview" in sys.modules:
        return

    stub = types.ModuleType("openreview")

    class DummyOpenReviewException(Exception):
        pass

    class DummyOpenReviewClient:
        def __init__(self, *args, **kwargs):
            pass

    stub.OpenReviewException = DummyOpenReviewException
    stub.api = types.SimpleNamespace(OpenReviewClient=DummyOpenReviewClient)
    sys.modules["openreview"] = stub


_install_openreview_stub()

from openreview_scraper import __version__
from openreview_scraper.cli import DB_COMMANDS, TOP_LEVEL_COMMANDS, WORKER_COMMANDS, cli


class CLIContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_top_level_command_surface_matches_plan(self) -> None:
        self.assertTrue(set(TOP_LEVEL_COMMANDS).issubset(set(cli.commands)))
        self.assertIn("db", cli.commands)
        self.assertIn("worker", cli.commands)

    def test_db_subcommands_match_planned_contract(self) -> None:
        db_group = cli.commands["db"]
        self.assertEqual(set(db_group.commands), set(DB_COMMANDS))

    def test_worker_subcommands_match_planned_contract(self) -> None:
        worker_group = cli.commands["worker"]
        self.assertEqual(set(worker_group.commands), set(WORKER_COMMANDS))

    def test_version_option_uses_package_version(self) -> None:
        result = self.runner.invoke(cli, ["--version"], prog_name="openreview-scraper")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn(__version__, result.output)

    def test_main_without_command_prints_help(self) -> None:
        result = self.runner.invoke(cli, [], prog_name="openreview-scraper")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("openreview-scraper", result.output)
        self.assertIn("fetch", result.output)
        self.assertIn("worker", result.output)


if __name__ == "__main__":
    unittest.main()
