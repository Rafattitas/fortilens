# Contributing to FortiLens

Thank you for helping make FortiGate observability easier to deploy and understand.

Useful first contributions include documentation fixes, FortiOS compatibility reports, sanitized inventory fixtures, and accounting edge cases. Discuss broad architecture or new service dependencies in an issue before implementing them.

## Local checks

Python 3.10+ is enough for the source test suite; no pip dependencies are required.

```bash
python3 -m unittest discover -s tests -v
python3 portable/setup.py bundle --destination dist/fortilens-single-device.zip
```

Use synthetic fixtures and separate directories, ports, Compose projects, and volumes for integration work. Never use a client's production environment as a test fixture. Include meaningful regression coverage for behavior changes and document what you actually verified.

## Design principles

- Keep missing evidence visible; do not turn absent samples into UP/DOWN or zero usage.
- Prefer existing exporter metadata over adding another collector.
- Separate reported role, interface type, explicit monitoring choices, and accounting filters.
- Keep FortiGate operations read-only and explicit; never silently weaken TLS or overwrite dashboard edits.
- Keep shipped messages and documentation in English, with plain wording and actionable errors.
- Keep secrets, generated installations, raw logs, and customer screenshots out of commits and issues.

Open a focused pull request explaining the trigger, resulting behavior, tests, and remaining limits. Contributions are distributed under the repository's MIT license. Follow the [security policy](SECURITY.md) for sensitive findings.
