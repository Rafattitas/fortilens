"""Source checks use synthetic temporary files, never a deployment directory."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_repository as checks


class RepositoryChecksTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def write(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def test_markdown_checks_nested_links_anchors_and_ignores_examples(self):
        target = self.write("docs/guide.md", "# Start here\n# Start here\n")
        readme = self.write(
            "README.md",
            "[guide](docs/guide.md#start-here-1)\n[external](https://example.invalid/)\n"
            "```markdown\n[example](not-a-real-file.md)\n```\n",
        )
        self.assertEqual(checks.check_markdown(self.root, [readme, target]), [])

    def test_markdown_rejects_missing_files_fragments_and_repository_escape(self):
        self.write("docs/guide.md", "# Known heading\n")
        readme = self.write(
            "README.md",
            "[missing](missing.md)\n[fragment](docs/guide.md#missing)\n[escape](../outside.md)\n",
        )
        errors = checks.check_markdown(self.root, [readme])
        self.assertEqual(len(errors), 3)
        self.assertTrue(any("leaves repository" in error for error in errors))

    def test_python_grammar_preserves_different_runtime_minimums(self):
        portable = self.write("portable/sample.py", "try:\n pass\nexcept* ValueError:\n pass\n")
        management = self.write("management/sample.py", portable.read_text())
        errors = checks.check_python(self.root, [portable, management])
        self.assertEqual(len(errors), 1)
        self.assertIn("portable", errors[0])

    def test_sensitive_filenames_are_checked_without_reading_file_contents(self):
        paths = [
            self.root / name
            for name in (
                "site.json",
                "secrets/token",
                "auth.sqlite",
                "tls.crt",
                "capture.har",
                "exports/customer.csv",
            )
        ]
        self.assertEqual(len(checks.check_sensitive_paths(self.root, paths)), len(paths))
        allowed = [self.root / name for name in ("site.example.json", ".env.example", "auth.py")]
        self.assertEqual(checks.check_sensitive_paths(self.root, allowed), [])

    @unittest.skipUnless(
        importlib.util.find_spec("yaml"), "YAML checks need development dependencies"
    )
    def test_yaml_rejects_duplicate_keys_and_floating_remote_actions(self):
        workflow = self.write(
            ".github/workflows/test.yml",
            "name: test\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n"
            "    steps:\n      - uses: actions/checkout@main\n",
        )
        self.assertIn("not pinned", checks.check_yaml(self.root, [workflow])[0])
        duplicate = self.write("duplicate.yml", "version: 1\nversion: 2\n")
        self.assertIn("Duplicate", checks.check_yaml(self.root, [duplicate])[0])

    @unittest.skipUnless(
        importlib.util.find_spec("yaml"), "YAML checks need development dependencies"
    )
    def test_yaml_accepts_pinned_workflow_and_local_action(self):
        workflow = self.write(
            ".github/workflows/test.yml",
            "name: test\non: push\npermissions:\n  contents: read\njobs:\n"
            "  test:\n    runs-on: ubuntu-latest\n    steps:\n"
            f"      - uses: actions/checkout@{'a' * 40}\n      - uses: ./.github/actions/local\n",
        )
        self.assertEqual(checks.check_yaml(self.root, [workflow]), [])


if __name__ == "__main__":
    unittest.main()
