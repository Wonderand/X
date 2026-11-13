"""Online SQLite backup, including committed WAL data."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
from app.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, help='A new local .sqlite3 output file')
    args = parser.parse_args()
    source = Settings.from_env().data_dir / 'xops.sqlite3'
    destination = Path(args.output).resolve()
    if not source.exists():
        raise SystemExit('Application database does not exist')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids accidentally replacing a previous backup.
    with destination.open('xb'):
        pass
    with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            reader.backup(writer)
    print(f'Backup saved: {destination}')


if __name__ == '__main__':
    main()
