import csv
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

import load_seed

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "app.db"
    load_seed.load(db_path=path)
    return path


def rows(db_path, sql):
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchall()


def columns(db_path, table):
    return [(name, kind) for _, name, kind, *_ in rows(db_path, f"PRAGMA table_info({table})")]


def test_row_counts_match_csvs(db):
    assert rows(db, "SELECT COUNT(*) FROM tickets") == [(24,)]
    assert rows(db, "SELECT COUNT(*) FROM customers") == [(20,)]


def test_columns_match_csv_headers(db):
    assert columns(db, "tickets") == [
        ("ticket_id", "TEXT"),
        ("customer_id", "TEXT"),
        ("created_at", "TEXT"),
        ("text", "TEXT"),
    ]
    assert columns(db, "customers") == [
        ("customer_id", "TEXT"),
        ("name", "TEXT"),
        ("plan", "TEXT"),
        ("open_tickets", "INTEGER"),
    ]


def test_open_tickets_stored_as_integer(db):
    assert rows(db, "SELECT DISTINCT typeof(open_tickets) FROM customers") == [("integer",)]


def test_second_run_gives_identical_contents(db):
    before = {t: rows(db, f"SELECT * FROM {t} ORDER BY 1") for t in ("tickets", "customers")}
    load_seed.load(db_path=db)
    after = {t: rows(db, f"SELECT * FROM {t} ORDER BY 1") for t in ("tickets", "customers")}
    assert after == before
    assert len(after["tickets"]) == 24
    assert len(after["customers"]) == 20


def test_rows_match_csvs_field_by_field(db):
    for table in ("tickets", "customers"):
        with (ROOT / "seed" / f"{table}.csv").open(newline="", encoding="utf-8") as f:
            expected = [tuple(row.values()) for row in csv.DictReader(f)]
        loaded = [tuple(str(v) for v in row) for row in rows(db, f"SELECT * FROM {table} ORDER BY rowid")]
        assert loaded == expected


def test_paths_resolve_from_script_location():
    assert load_seed.DB_PATH == ROOT / "app.db"
    assert load_seed.SEED_DIR == ROOT / "seed"


def test_failed_load_leaves_existing_db_untouched(db, monkeypatch):
    # Fail on the customers insert, after tickets has already been dropped, recreated and filled.
    read_rows = load_seed._read_rows
    monkeypatch.setattr(
        load_seed, "_read_rows", lambda path, cols: read_rows(path, cols) if path.name == "tickets.csv" else [("C-01",)]
    )
    before = {t: rows(db, f"SELECT * FROM {t} ORDER BY 1") for t in ("tickets", "customers")}
    with pytest.raises(sqlite3.ProgrammingError):
        load_seed.load(db_path=db)
    after = {t: rows(db, f"SELECT * FROM {t} ORDER BY 1") for t in ("tickets", "customers")}
    assert after == before


@pytest.fixture
def triage_server(db, monkeypatch):
    spec = importlib.util.spec_from_file_location("triage_server", ROOT / "mcp" / "triage_server.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DB_PATH", db)
    return module


def test_mcp_server_reads_loaded_db(triage_server):
    ticket = triage_server.get_ticket("T-1042")
    assert ticket["customer_id"] == "C-77"
    customer = triage_server.get_customer_history("C-77")
    assert (customer["name"], customer["plan"], customer["open_tickets"]) == ("Northwind", "Enterprise", 2)
