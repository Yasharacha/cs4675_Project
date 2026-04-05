import argparse
import json
import os
import sqlite3
import subprocess
from pathlib import Path


def print_rows(rows: list[dict], label: str) -> None:
    if not rows:
        print(f"No URL mappings found in {label}")
        return

    print(f"Database: {label}")
    print(f"Total rows: {len(rows)}")
    print()

    for record in rows:
        for key, value in record.items():
            print(f"{key}: {value}")
        print("-" * 40)


def read_local_db(database_path: str) -> None:
    db_file = Path(database_path)

    if not db_file.exists():
        print(f"Database file not found: {db_file}")
        return

    connection = sqlite3.connect(db_file)
    connection.row_factory = sqlite3.Row

    rows = connection.execute(
        """
        SELECT id, code, long_url, created_at, expires_at, click_count, last_accessed_at
        FROM url_mappings
        ORDER BY id
        """
    ).fetchall()

    print_rows([dict(row) for row in rows], str(db_file))


def read_docker_db(service: str) -> None:
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        service,
        "python",
        "-c",
        (
            "import json, sqlite3; "
            "conn = sqlite3.connect('/app/data/url_shortener.db'); "
            "conn.row_factory = sqlite3.Row; "
            "rows = conn.execute("
            "\"SELECT id, code, long_url, created_at, expires_at, click_count, last_accessed_at "
            "FROM url_mappings ORDER BY id\""
            ").fetchall(); "
            "print(json.dumps([dict(r) for r in rows]))"
        ),
    ]

    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
    except FileNotFoundError:
        print("Docker is not installed or not on PATH.")
        return
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip() or "Unknown Docker error."
        print(f"Could not read Docker database from service '{service}': {stderr}")
        return

    rows = json.loads(result.stdout or "[]")
    print_rows(rows, f"docker:{service}:/app/data/url_shortener.db")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Print URL shortener database contents.")
    parser.add_argument(
        "--docker",
        action="store_true",
        help="Read the database from a running Docker Compose backend container instead of the local file.",
    )
    parser.add_argument(
        "--service",
        default="backend1",
        help="Compose service name to inspect when using --docker. Default: backend1",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.docker:
        read_docker_db(args.service)
        return

    database_path = os.getenv("DATABASE_PATH", "data/url_shortener.db")
    read_local_db(database_path)


if __name__ == "__main__":
    main()
