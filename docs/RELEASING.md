# Release preparation and distributions

FortiLens has two distribution forms. Published releases and existing assets are
immutable: this guide prepares a future release; it does not publish one or
upgrade an installation. The existing v0.2.0 portable release predates web
management and remains available unchanged.

| Artifact | Layout and purpose |
|---|---|
| `fortilens-vX.Y.Z-full.tar.gz` | `fortilens/` source root with web management, Ubuntu installer, portable monitoring/reporting, runtime assets, public documentation and development checks. |
| `fortilens-vX.Y.Z.zip` | Existing `forti-monitor/` portable layout with its 23 reviewed files; no web management or host installer. |
| `SHA256SUMS.txt` | SHA256 checksums for both archives. |

The portable `python portable/setup.py bundle --destination ...` command is
unchanged. The release builder obtains its payload through that command and
normalizes only ZIP metadata. Names and file bytes match the bundle built from
the same reviewed source. Published historical ZIP bytes are never replaced.

## Version policy

Use a `vMAJOR.MINOR.PATCH` tag, optionally with a SemVer prerelease such as
`v0.3.0-rc.1`. Version numbers in commands below are examples, not an announced
release or a compatibility promise. While the project remains early access,
document every compatibility-impacting change explicitly; a minor version is
not permission to migrate accounting identity, databases or installation data.
Use patch releases for compatible fixes and a minor release for compatible new
capabilities. A breaking change requires a reviewed migration plan and a clearly
identified release boundary. Keep meaningful changes in [the changelog](../CHANGELOG.md).

Tag only an explicitly approved, final, committed revision after all required
checks succeed. Do not retag or force-push. Release publication is a maintainer
decision, separate from source merges, artifact building and production rollout.

## Build and verify locally

Use Python 3.10+ for the standard-library release builder. Complete management
tests require Python 3.11+ and the separately pinned test dependencies. Start
from a clean reviewed checkout and use a new output directory:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r management/requirements-test.txt -r requirements-dev.txt
python -m unittest discover -s tests -v
python tests/check_frontend.py
python tools/check_repository.py
python tools/release.py build --version v0.3.0-rc.1 --output dist
python tools/release.py verify --version v0.3.0-rc.1 --directory dist
(cd dist && sha256sum --check SHA256SUMS.txt)
```

On Windows, activate `.venv\Scripts\Activate.ps1`; the release `verify` command
also checks SHA256, so `sha256sum` is optional there. Node.js is used only for
frontend syntax verification. Linux/amd64, Docker Compose v2 and the documented
Ubuntu prerequisites remain necessary to run the actual services.

`build` refuses to overwrite either archive or `SHA256SUMS.txt`. `verify` compares
both archive payloads to the current source checkout, so run it against the same
revision and unchanged files used for the build. Do not edit documentation or
source between building, testing and attaching the artifacts.

## Completeness, exclusions and reproducibility

`tools/release.py` combines the installer's existing `source_files()` allowlist
with an explicit list of public source/development files. The full artifact
contains `install.sh`, every installer/runtime input, both Dockerfiles, all
monitoring templates and frontend assets. A SHA256 manifest records the source
files and executable installer mode. No recursive workspace copy is used.
It records the Git revision and whether the build worktree was modified; a local
candidate with uncommitted changes is never represented as a clean tagged build.
An extracted full archive retains that provenance without requiring Git history.

Generated installations, site-specific configuration, credentials, raw logs,
SQLite databases, customer exports, screenshots, Git history and unlisted notes
are excluded. Missing files and symlinks fail the build. Adding a required
runtime file requires reviewing the installer, Docker and release allowlists
together and extending their regression checks; never broaden a directory copy.

Source archive ordering, timestamps, ownership and modes are fixed. Gzip headers
and portable ZIP metadata are normalized. Two builds with identical source bytes,
version, Python/compression implementation and builder produce identical bytes;
cross-version compression output is not promised to be identical. Checksums prove
artifact consistency, not publisher identity or absence of vulnerabilities.

## Validate the packaged source

The unit tests verify full/portable content and SHA256, reproducibility, unsafe
archive members, exclusion of private fixtures and non-overwrite behavior. They
extract the full artifact, check the installer's required inputs, run existing
CLI help commands and execute synthetic accounting regression tests from it.
These checks never install services or contact a firewall.

The release workflow tests extracted artifacts on disposable Ubuntu 24.04 VMs:

1. The full unit suite, shipped JavaScript syntax and isolated accounting pipeline.
2. The existing nonroot web/helper/real-Docker management acceptance scenario.
3. The actual packaged executable installer, run twice, followed by the guarded
   systemd acceptance scenario on that ephemeral GitHub Actions VM.

All three must pass before release assets can be attached. Source-checkout CI
continues separately; successful fresh installation alone does not demonstrate
existing-install compatibility. The management integration also exercises
adoption, settings/rollback and persistent-state preservation with synthetic data.
See [QA](QA.md) and [management verification](MANAGEMENT_VERIFICATION.md).

Never run root/systemd acceptance or `--installed` mode on a developer, company
or production host. The local machine may be unsuitable for Docker/Ubuntu tests;
report those checks as unexecuted until the exact candidate's isolated CI runs.
Browser usability, real FortiOS compatibility, traffic completeness and deployment
capacity still require their separate evidence. A release must not claim those
properties from archive, unit or synthetic Docker checks.

## Review and publication sequence

1. Review the focused changes, changelog, source compatibility and dependency pins.
2. Run source CI and the **Release distributions** PR workflow. The latter runs
   for every pull request, including documentation changes that enter the full
   artifact, and retains candidate artifacts for seven days. There are no path
   filters that could leave a required check pending. These candidates use a
   clearly non-release CI version.
3. Optionally run that workflow manually with a prospective version. Manual runs
   build/test artifacts only; they have no release publication step.
4. Review the packaged source, checksums and extracted-artifact results. Resolve
   failures without bypassing tests; obtain explicit approval for merging/tagging.
5. After a maintainer explicitly publishes the approved tag's GitHub release, the
   workflow rebuilds that tag, verifies/tests its actual artifacts and attaches
   them using `gh release upload` without `--clobber`. Only this final job has
   `contents: write`; it neither creates tags nor creates/publishes releases.
6. Verify the two uploaded archives and checksums. Retain the exact source/tag
   and test URLs in the release notes. A failed upload must never be repaired by
   replacing an existing published file; review what already exists first.

A company rollout remains a separate authorized operational change. Existing
installations need explicit adoption with their original Compose project, or a
reviewed update procedure; extracting an archive or rerunning bootstrap does
not authorize changing a live installation. See [web setup](WEB_SETUP.md) and
[upgrades](UPGRADE.md).
