"""Local emergency recovery; never imported as an HTTP endpoint."""

import argparse
from contextlib import closing
import getpass
from pathlib import Path
import secrets
import sqlite3
import sys
import warnings
from .auth import Auth, digest
from .common import Problem, atomic, report_failure


def _recover():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data", type=Path, required=True)
    p.add_argument("--rotate-bootstrap", action="store_true")
    a = p.parse_args()
    token = a.data / "bootstrap.token"
    if not (a.data / "auth.sqlite").exists():
        raise SystemExit("No registered management database. Use install.sh first.")
    if a.rotate_bootstrap and not token.exists():
        with closing(sqlite3.connect(a.data / "auth.sqlite")) as db:
            if not db.execute("SELECT 1 FROM admin").fetchone():
                atomic(token, (secrets.token_urlsafe(32) + "\n").encode())
    auth = Auth(a.data, token)
    try:
        if a.rotate_bootstrap:
            if auth.exists():
                raise SystemExit("Administrator already exists; bootstrap cannot be reopened.")
            value = secrets.token_urlsafe(32)
            with auth.lock, auth.db:
                auth.db.execute("DELETE FROM bootstrap")
                auth.db.execute("INSERT INTO bootstrap VALUES(?)", (digest(value),))
            atomic(token, (value + "\n").encode())
            print("A new one-time bootstrap token is stored in " + str(token))
            return
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass("New administrator password: ")
            second = getpass.getpass("Confirm password: ")
        if first != second:
            raise SystemExit("Passwords do not match.")
        auth.recover(first)
        print(
            "Administrator recovered. All management sessions were revoked. Monitoring data and service credentials were unchanged."
        )
    finally:
        try:
            auth.close()
        except sqlite3.Error as exc:
            report_failure("recovery_cleanup", exc)


def main():
    try:
        _recover()
    except Problem as exc:
        raise SystemExit(exc.message) from None
    except getpass.GetPassWarning:
        raise SystemExit(
            "Hidden password input is unavailable. Run recovery from an interactive terminal."
        ) from None
    except EOFError:
        raise SystemExit(
            "Recovery input ended before confirmation. No recovery was requested."
        ) from None
    except KeyboardInterrupt:
        print("Administrator recovery cancelled.", file=sys.stderr)
        raise SystemExit(130) from None
    except Exception as exc:
        report_failure("recovery_operation", exc)
        raise SystemExit(
            "Administrator recovery could not complete safely. Review local permissions and storage; no automatic retry was attempted."
        ) from None


if __name__ == "__main__":
    main()
