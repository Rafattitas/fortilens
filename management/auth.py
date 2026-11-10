"""Single initial administrator, salted scrypt hashes and revocable server sessions."""

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from .common import Problem, atomic, report_failure

PASSWORD_WORKERS = threading.Semaphore(2)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise Problem("password_policy", "Use a password between 12 and 256 characters.")
    salt = salt or secrets.token_bytes(16)
    with PASSWORD_WORKERS:
        key = hashlib.scrypt(password.encode(), salt=salt, n=32768, r=8, p=1, maxmem=67108864)
    return "scrypt$32768$8$1$" + salt.hex() + "$" + key.hex()


def verify(password, stored):
    try:
        return hmac.compare_digest(
            password_hash(password, bytes.fromhex(stored.split("$")[4])), stored
        )
    except (ValueError, TypeError, IndexError, AttributeError, Problem):
        return False


# Verified for an unknown username, so its timing matches a wrong password.
ABSENT_PASSWORD = "scrypt$32768$8$1$" + "00" * 16 + "$" + "00" * 64


class Auth:
    def __init__(self, directory, bootstrap_file, ttl=28800):
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.bootstrap_file = Path(bootstrap_file)
        self.ttl = ttl
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / "auth.sqlite", check_same_thread=False)
        try:
            self._initialize()
        except Exception:
            try:
                self.db.close()
            except sqlite3.Error as exc:
                report_failure("auth_initialization_cleanup", exc)
            raise

    def _initialize(self):
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS admin(id INTEGER PRIMARY KEY CHECK(id=1), username TEXT NOT NULL, password TEXT NOT NULL, role TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS session(token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS attempts(address TEXT PRIMARY KEY, since REAL NOT NULL, count INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS bootstrap(digest TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS audit(time REAL NOT NULL, event TEXT NOT NULL, result TEXT NOT NULL);
        """)
        if not self.exists() and not self.db.execute("SELECT 1 FROM bootstrap").fetchone():
            if not self.bootstrap_file.is_file():
                raise Problem(
                    "bootstrap_missing", "Generate a bootstrap token using the local recovery tool."
                )
            token = self.bootstrap_file.read_text().strip()
            if len(token) < 32:
                raise Problem(
                    "bootstrap_invalid",
                    "The bootstrap token must have at least 32 random characters.",
                )
            with self.db:
                self.db.execute("INSERT INTO bootstrap VALUES(?)", (digest(token),))
        (self.root / "auth.sqlite").chmod(0o600)

    def exists(self):
        return self.db.execute("SELECT 1 FROM admin").fetchone() is not None

    def close(self):
        with self.lock:
            self.db.close()

    def audit(self, event, result="success"):
        with self.db:
            self.db.execute("INSERT INTO audit VALUES(?,?,?)", (time.time(), event, result))

    def limit(self, address):
        now = time.time()
        with self.lock, self.db:
            self.db.execute("DELETE FROM attempts WHERE since < ?", (now - 60,))
            row = self.db.execute(
                "SELECT since,count FROM attempts WHERE address=?", (address,)
            ).fetchone()
            if (
                row
                and row[1] >= 10
                or not row
                and self.db.execute("SELECT count(*) FROM attempts").fetchone()[0] >= 1024
            ):
                raise Problem("rate_limited", "Too many attempts. Wait one minute.", 429)
            self.db.execute(
                "INSERT OR REPLACE INTO attempts VALUES(?,?,?)",
                (address, row[0] if row else now, (row[1] if row else 0) + 1),
            )

    def create(self, username, password, confirmation, token):
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,63}", username) or password != confirmation:
            raise Problem("invalid_admin", "Choose a valid username and matching passwords.")
        hashed = password_hash(password)
        with self.lock, self.db:
            if self.exists():
                raise Problem("bootstrap_closed", "The administrator already exists.", 409)
            row = self.db.execute("SELECT digest FROM bootstrap").fetchone()
            if not row or not hmac.compare_digest(digest(token), row[0]):
                raise Problem("bootstrap_rejected", "The bootstrap token was rejected.", 403)
            self.db.execute("INSERT INTO admin VALUES(1,?,?,?)", (username, hashed, "admin"))
            self.db.execute("DELETE FROM bootstrap")
            self.audit("administrator_created")
        # A leftover token file is harmless: the endpoint is irreversibly closed
        # by the administrator row, not by a mutable file existence check.
        try:
            self.bootstrap_file.unlink(missing_ok=True)
        except OSError as exc:
            report_failure("bootstrap_token_cleanup", exc)

    def login(self, username, password, address):
        self.limit(address)
        with self.lock, self.db:
            row = self.db.execute("SELECT username,password,role FROM admin").fetchone()
            known = bool(row) and username == row[0]
            matched = verify(password, row[1] if known else ABSENT_PASSWORD)
            if not known or not matched or row[2] != "admin":
                self.audit("login", "rejected")
                raise Problem("login_rejected", "Username or password was rejected.", 401)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            self.db.execute("DELETE FROM session WHERE expires <= ?", (time.time(),))
            self.db.execute(
                "INSERT INTO session VALUES(?,?,?)", (digest(token), csrf, time.time() + self.ttl)
            )
            self.audit("login")
            return token, csrf

    def session(self, token):
        with self.lock:
            row = self.db.execute(
                "SELECT csrf FROM session WHERE token=? AND expires>?", (digest(token), time.time())
            ).fetchone()
            user = self.db.execute("SELECT username,role FROM admin").fetchone()
            if not row or not user or user[1] != "admin":
                raise Problem("authentication_required", "Please sign in as an administrator.", 401)
            return {"username": user[0], "role": user[1], "csrf": row[0]}

    def logout(self, token):
        with self.lock, self.db:
            self.db.execute("DELETE FROM session WHERE token=?", (digest(token),))
            self.audit("logout")

    def change_password(self, old, new, confirmation):
        hashed = password_hash(new)
        with self.lock, self.db:
            row = self.db.execute("SELECT password FROM admin").fetchone()
            if not row or not verify(old, row[0]) or new != confirmation:
                raise Problem("password_rejected", "Review the current password and confirmation.")
            self.db.execute("UPDATE admin SET password=?", (hashed,))
            self.db.execute("DELETE FROM session")
            self.audit("password_changed")

    def recover(self, password):
        """Host-local administrator recovery only; never expose this on HTTP."""
        hashed = password_hash(password)
        with self.lock, self.db:
            if not self.exists():
                raise Problem("admin_missing", "Use first-run bootstrap instead.")
            self.db.execute("UPDATE admin SET password=?", (hashed,))
            self.db.execute("DELETE FROM session")
            self.audit("administrator_recovered")
