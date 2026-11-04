"""Disposable Linux Docker host-network acceptance; never targets an installation."""

import contextlib
import io
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import setup


def main():
    if sys.platform != "linux":
        raise SystemExit("Requires an isolated rootful Linux Docker host")
    with tempfile.TemporaryDirectory(prefix="fortilens-native-host-") as temporary:
        root = Path(temporary) / "installation"
        # Reserve a TCP handoff and verify the adjacent private health port.
        sink = socket.socket()
        while True:
            sink.bind(("127.0.0.1", 0))
            port = sink.getsockname()[1]
            try:
                with socket.socket() as health:
                    health.bind(("127.0.0.1", port + 1))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    udp.bind(("0.0.0.0", port))
                break
            except (OSError, OverflowError):
                sink.close()
                sink = socket.socket()
        sink.listen(1)
        sink.settimeout(90)
        config = setup.validate_config(
            {
                "fortigate_host": "192.0.2.10",
                "syslog_format": "fortigate",
                "syslog_protocol": "udp",
                "syslog_network": "host",
                "syslog_port": port,
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            setup.render(config, root, "synthetic-only-token")
        compose = setup.load(root / "compose.yaml")
        native = compose["services"]["fortilens-syslog"]
        image = (
            (Path(__file__).resolve().parents[1] / "portable/templates/Dockerfile.syslog")
            .read_text()
            .splitlines()[0]
            .split()[1]
        )
        sleeper = {"image": image, "command": ["python", "-c", "import time; time.sleep(600)"]}
        compose["services"] = {
            "fortilens-syslog": native,
            "alloy": sleeper,
            "denied-sender": sleeper,
        }
        (root / "compose.yaml").write_text(setup.dumps(compose), encoding="utf-8")
        command = setup.compose_command(root)

        def docker(*args):
            result = subprocess.run(
                [*command, *args],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if result.returncode:
                print(result.stderr[-4000:], file=sys.stderr)
            result.check_returncode()
            return result.stdout.strip()

        packets = []
        errors = []
        finished = threading.Event()

        def receive():
            try:
                connection, _ = sink.accept()
                with connection:
                    connection.settimeout(30)
                    while not finished.is_set():
                        data = connection.recv(65536)
                        if not data:
                            break
                        packets.append(data)
            except Exception as exc:
                if not finished.is_set():
                    errors.append(type(exc).__name__)

        thread = threading.Thread(target=receive, daemon=True)
        try:
            docker("config", "--quiet")
            docker("build", "fortilens-syslog")
            docker("up", "-d", "alloy", "denied-sender")
            identifier = docker("ps", "-q", "alloy")
            row = json.loads(
                subprocess.run(
                    ["docker", "inspect", identifier],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout
            )[0]
            network = next(iter(row["NetworkSettings"]["Networks"].values()))
            config["fortigate_host"] = network["IPAddress"]
            (root / "site.json").write_text(setup.dumps(config), encoding="utf-8")
            thread.start()
            docker("up", "-d", "--no-deps", "fortilens-syslog")

            def health():
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port + 1}/health", timeout=2
                ) as response:
                    return json.load(response)

            def wait(predicate):
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    try:
                        result = predicate()
                        if result:
                            return result
                    except (OSError, ValueError):
                        pass
                    time.sleep(0.25)
                raise AssertionError("Synthetic host reception did not reach expected state")

            wait(health)
            body = 'date=2026-11-03 time=01:00:00 devid="SYNTHETIC" sentbyte=12 rcvdbyte=34 eventtime=123456789'

            def send(service):
                docker(
                    "exec",
                    "-T",
                    service,
                    "python",
                    "-c",
                    "import socket,sys; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.sendto(sys.argv[1].encode(),(sys.argv[2],int(sys.argv[3])))",
                    "<134>" + body,
                    network["Gateway"],
                    str(port),
                )

            send("denied-sender")
            wait(lambda: health()["rejected_reasons"]["source"] == 1)
            send("alloy")
            wait(lambda: health()["handed_to_tcp"] == 1 and body.encode() in b"".join(packets))
            assert not errors, errors
            wire = b"".join(packets)
            size, message = wire.split(b" ", 1)
            assert int(size) == len(message) and message.endswith(body.encode())
            assert health()["received_datagrams"] == 2
            # The main receiver must drop its bootstrap identity and all usable
            # capabilities before receiving. Docker exec's identity is separate.
            docker(
                "exec",
                "-T",
                "fortilens-syslog",
                "python",
                "-c",
                "from pathlib import Path; d=dict(x.split(':',1) for x in Path('/proc/1/status').read_text().splitlines() if ':' in x); assert set(d['Uid'].split())=={'10001'}; assert set(d['Gid'].split())=={'10001'}; assert all(int(d[k].strip(),16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb')); assert d['NoNewPrivs'].strip()=='1'",
            )
            # Conventional low ports use the same bind-then-drop implementation.
            # Fail if another process on this disposable runner owns either port.
            docker(
                "run",
                "--rm",
                "--no-deps",
                "fortilens-syslog",
                "python",
                "-c",
                "import os,socket,syslog_receiver; s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.bind(('127.0.0.1',514)); h=socket.socket(); h.bind(('127.0.0.1',515)); syslog_receiver.drop_host_privileges(); assert os.geteuid()==10001; s.close(); h.close()",
            )
            print(
                json.dumps(
                    {
                        "native_host_source_preservation": "PASS",
                        "different_sender": "REJECTED",
                        "original_body": "PRESERVED",
                        "nonroot_low_port": "PASS",
                        "synthetic_only": True,
                    }
                )
            )
        finally:
            finished.set()
            if sys.exc_info()[0] is not None:
                subprocess.run(
                    [*command, "logs", "--tail", "10", "fortilens-syslog"],
                    cwd=root,
                    check=False,
                    timeout=20,
                )
            subprocess.run(
                [*command, "down", "--volumes", "--remove-orphans"],
                cwd=root,
                check=True,
                timeout=120,
                capture_output=True,
            )
            sink.close()
            if thread.is_alive():
                thread.join(5)


if __name__ == "__main__":
    main()
