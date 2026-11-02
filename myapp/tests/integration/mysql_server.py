"""Disposable native MySQL for macOS CI, where Docker is unavailable.

The server owns a new temporary data directory, socket, port, and password. It
never reads the developer's MySQL configuration or connects to an existing DB.
"""
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import mysql.connector


def wait_for_mysql(server, timeout=120, process=None):
    deadline = time.monotonic() + timeout
    while True:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Test mysqld exited with status {process.returncode}.")
        try:
            mysql.connector.connect(**server, connection_timeout=2).close()
            return
        except mysql.connector.Error:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)


@contextmanager
def native_mysql(schema):
    mysqld, mysql = shutil.which("mysqld"), shutil.which("mysql")
    if not mysqld or not mysql:
        raise RuntimeError("Native integration tests need MySQL 8.4 binaries on PATH (brew install mysql@8.4).")
    version = subprocess.run([mysqld, "--no-defaults", "--version"], capture_output=True, text=True,
                             check=True, timeout=10)
    if not re.search(r"\bVer 8\.4\.", version.stdout):
        raise RuntimeError(f"Integration tests require MySQL 8.4; found {version.stdout.strip()}.")

    # Keep the Unix socket path below macOS's length limit, even in long CI workspaces.
    with tempfile.TemporaryDirectory(prefix="adam-pytest-mysql-", dir="/tmp") as directory:
        root = Path(directory)
        data = root / "data"
        log = root / "mysql.log"
        password = secrets.token_urlsafe(24)
        init = root / "init.sql"
        init.write_text(f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{password}';\n", encoding="utf-8")
        init.chmod(0o600)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        server = {"host": "127.0.0.1", "port": port, "user": "root", "password": password}

        with log.open("wb") as output:
            subprocess.run([mysqld, "--no-defaults", "--initialize-insecure", f"--datadir={data}"],
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=120)
            process = subprocess.Popen([
                mysqld, "--no-defaults", f"--datadir={data}", f"--socket={root / 'mysql.sock'}",
                f"--pid-file={root / 'mysql.pid'}", "--bind-address=127.0.0.1", f"--port={port}",
                "--mysqlx=OFF", "--skip-log-bin", f"--init-file={init}",
            ], stdout=output, stderr=subprocess.STDOUT)
            try:
                try:
                    wait_for_mysql(server, process=process)
                    with schema.open("rb") as sql:
                        subprocess.run([
                            mysql, "--no-defaults", "--no-login-paths", "--protocol=TCP",
                            "--host=127.0.0.1", f"--port={port}", "--user=root", "--default-character-set=utf8mb4",
                        ], stdin=sql, env=os.environ | {"MYSQL_PWD": password},
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=120)
                except Exception as error:
                    output.flush()
                    details = log.read_text(encoding="utf-8", errors="replace").replace(password, "[redacted]")
                    raise RuntimeError(f"Disposable MySQL setup failed:\n{details}") from error
                yield server
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)
