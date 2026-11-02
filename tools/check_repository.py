"""Check source hygiene without importing applications or contacting a service.

Python grammar and local Markdown links use only the standard library. YAML
validation additionally needs PyYAML from requirements-dev.txt. These checks do
not replace GitHub's workflow validator, a secret scanner or integration tests.
"""

from __future__ import annotations

import argparse
import ast
import html
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "venv", "node_modules", "dist", "__pycache__", ".ruff_cache"}
PINNED_ACTION = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")


class RepositoryCheckError(ValueError):
    """A fixed diagnostic that does not contain submitted file contents."""


def source_files(root: Path) -> list[Path]:
    """Use Git's source view, or the extracted archive when no checkout exists."""
    if (root / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
            timeout=30,
        )
        names = result.stdout.decode("utf-8").split("\0")
        return sorted({root / name for name in names if name and (root / name).is_file()})
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in EXCLUDED for part in path.relative_to(root).parts)
    )


def without_fences(text: str) -> str:
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if marker:
            if fence is None:
                fence = marker[1][0]
            elif marker[1][0] == fence:
                fence = None
            lines.append("")
        else:
            lines.append(line if fence is None else "")
    return "\n".join(lines)


def markdown_anchors(text: str) -> set[str]:
    anchors, duplicates = set(), {}
    text = without_fences(text)
    for heading in re.findall(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.MULTILINE):
        heading = re.sub(r"<[^>]+>", "", html.unescape(heading))
        heading = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", heading)
        slug = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
        count = duplicates.get(slug, 0)
        duplicates[slug] = count + 1
        anchors.add(slug + (f"-{count}" if count else ""))
    anchors.update(re.findall(r"(?:id|name)=[\"']([^\"']+)[\"']", text))
    return anchors


def markdown_targets(text: str) -> list[str]:
    text = without_fences(text)
    inline = re.findall(r"!?\[[^\]\n]*\]\(\s*(<[^>]+>|[^\s)]+)", text)
    references = re.findall(r"^\s*\[[^]\n]+\]:\s*(<[^>]+>|\S+)", text, re.MULTILINE)
    embedded = re.findall(r"(?:href|src)=[\"']([^\"']+)[\"']", text)
    return inline + references + embedded


def check_markdown(root: Path, paths: list[Path]) -> list[str]:
    errors = []
    anchors: dict[Path, set[str]] = {}
    for path in paths:
        if path.suffix.lower() != ".md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            errors.append(f"{path.relative_to(root)}: Markdown source could not be read as UTF-8")
            continue
        for target in markdown_targets(text):
            try:
                parsed = urlsplit(target.strip("<>"))
            except ValueError:
                errors.append(f"{path.relative_to(root)}: Invalid link format; target suppressed")
                continue
            if parsed.scheme or parsed.netloc:
                continue
            destination = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path
            label = str(path.relative_to(root))
            if not destination.is_relative_to(root):
                errors.append(f"Local link leaves repository: {label}")
            elif not destination.exists():
                errors.append(f"Missing local link: {label}")
            elif parsed.fragment and destination.suffix.lower() == ".md":
                if destination not in anchors:
                    try:
                        anchors[destination] = markdown_anchors(
                            destination.read_text(encoding="utf-8")
                        )
                    except (OSError, UnicodeError):
                        errors.append(f"{label}: Linked Markdown source could not be read as UTF-8")
                        continue
                if unquote(parsed.fragment) not in anchors[destination]:
                    errors.append(f"Missing Markdown anchor: {label}")
    return errors


def check_python(root: Path, paths: list[Path]) -> list[str]:
    errors = []
    for path in paths:
        if path.suffix != ".py":
            continue
        relative = path.relative_to(root)
        version = (3, 11) if relative.parts[0] == "management" else (3, 10)
        try:
            ast.parse(
                path.read_text(encoding="utf-8"), filename=str(relative), feature_version=version
            )
        except SyntaxError as exc:
            errors.append(f"{relative}:{exc.lineno}: incompatible with Python {version}: {exc.msg}")
        except (OSError, UnicodeError):
            errors.append(f"{relative}: Python source could not be read as UTF-8")
    return errors


def check_sensitive_paths(root: Path, paths: list[Path]) -> list[str]:
    """Reject dangerous source filenames; never open or print potential secrets."""
    errors = []
    for path in paths:
        relative = path.relative_to(root)
        name = path.name.lower()
        sensitive = (
            any(
                part.lower() in {"secrets", ".fortilens", "private", "qa-private", "exports"}
                for part in relative.parts
            )
            or name == "site.json"
            or (name.startswith(".env") and name != ".env.example")
            or name in {"id_rsa", "id_ed25519", "fortigate-key.yaml"}
            or name.endswith(
                (
                    ".pem",
                    ".key",
                    ".crt",
                    ".p12",
                    ".pfx",
                    ".har",
                    ".sqlite",
                    ".sqlite3",
                    ".db",
                    ".log",
                )
            )
            or name.endswith(("-wal", "-shm"))
        )
        if sensitive:
            errors.append(f"Sensitive/generated filename in source: {relative}")
    return errors


def check_yaml(root: Path, paths: list[Path]) -> list[str]:
    try:
        import yaml
    except ImportError:
        return ["YAML checks require PyYAML; install requirements-dev.txt."]

    class UniqueLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            if key in result:
                raise RepositoryCheckError("Duplicate YAML mapping key")
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)
    errors = []
    for path in paths:
        if path.suffix not in {".yml", ".yaml"}:
            continue
        relative = path.relative_to(root)
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
            if relative.parts[:2] != (".github", "workflows"):
                continue
            if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
                raise RepositoryCheckError("Workflow needs a jobs mapping")
            if data.get("permissions") == "write-all":
                raise RepositoryCheckError("Use explicit workflow permissions instead of write-all")
            for job in data["jobs"].values():
                if not isinstance(job, dict):
                    raise RepositoryCheckError("Workflow jobs must be mappings")
                steps = job.get("steps", [])
                if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
                    raise RepositoryCheckError("Workflow steps must be mappings in a list")
                uses = [job["uses"]] if "uses" in job else []
                uses += [step["uses"] for step in steps if "uses" in step]
                for action in uses:
                    if not isinstance(action, str):
                        raise RepositoryCheckError("Workflow action references must be strings")
                    if not action.startswith("./") and not PINNED_ACTION.fullmatch(action):
                        raise RepositoryCheckError("Remote action is not pinned to a full commit")
        except RepositoryCheckError as exc:
            errors.append(f"{relative}: {exc}")
        except (yaml.YAMLError, ValueError, TypeError):
            errors.append(
                f"{relative}: Invalid YAML syntax or unsupported workflow structure; source contents suppressed"
            )
        except (OSError, UnicodeError):
            errors.append(f"{relative}: YAML source could not be read as UTF-8")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        if not root.is_dir():
            print("FAIL: Repository root is not an available directory", file=sys.stderr)
            return 1
        paths = source_files(root)
        failures = []
        for name, check in (
            ("Python grammar (portable 3.10+, management 3.11+)", check_python),
            ("Local Markdown links and anchors", check_markdown),
            ("Sensitive/generated source filenames", check_sensitive_paths),
            ("YAML syntax, duplicate keys and pinned workflow actions", check_yaml),
        ):
            problems = check(root, paths)
            failures.extend(problems)
            print(f"{'FAIL' if problems else 'PASS'}: {name}")
            for problem in problems:
                print(f"  {problem}")
        return 1 if failures else 0
    except (OSError, UnicodeError, subprocess.SubprocessError):
        print(
            "FAIL: Repository inputs could not be read or Git could not complete. Review file permissions and UTF-8 encoding; private details are suppressed.",
            file=sys.stderr,
        )
        return 1
    except KeyboardInterrupt:
        print("Repository validation cancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
