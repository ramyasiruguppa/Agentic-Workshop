"""Load seed/tickets.csv and seed/customers.csv into app.db. Safe to run repeatedly: each run rebuilds both tables."""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SEED_DIR = ROOT / "seed"
DB_PATH = ROOT / "app.db"

# Table name -> (CSV file, column name -> SQLite type). Column order and names match the CSV headers.
TABLES = {
    "tickets": ("tickets.csv", {"ticket_id": "TEXT", "customer_id": "TEXT", "created_at": "TEXT", "text": "TEXT"}),
    "customers": ("customers.csv", {"customer_id": "TEXT", "name": "TEXT", "plan": "TEXT", "open_tickets": "INTEGER"}),
}


def _read_rows(csv_path: Path, columns: dict[str, str]) -> list[tuple]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != list(columns):
            raise ValueError(f"{csv_path.name}: expected columns {list(columns)}, got {reader.fieldnames}")
        return [
            tuple(int(row[name]) if kind == "INTEGER" else row[name] for name, kind in columns.items())
            for row in reader
        ]


def load(db_path: Path = DB_PATH, seed_dir: Path = SEED_DIR) -> dict[str, int]:
    """Rebuild every table in db_path from the seed CSVs and return the row count per table."""
    data = {table: _read_rows(seed_dir / csv_name, columns) for table, (csv_name, columns) in TABLES.items()}
    # autocommit=False keeps the DROP/CREATE statements inside the transaction too, so a failure rolls back everything.
    conn = sqlite3.connect(db_path, autocommit=False)
    try:
        with conn:
            for table, (_, columns) in TABLES.items():
                column_defs = ", ".join(f"{name} {kind}" for name, kind in columns.items())
                placeholders = ", ".join("?" for _ in columns)
                conn.execute(f"DROP TABLE IF EXISTS {table}")
                conn.execute(f"CREATE TABLE {table} ({column_defs})")
                conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", data[table])
    finally:
        conn.close()
    return {table: len(rows) for table, rows in data.items()}


if __name__ == "__main__":
    counts = load()
    print(f"Loaded {counts['tickets']} tickets and {counts['customers']} customers into {DB_PATH.name}")
