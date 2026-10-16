"""Operational (core-banking) data plane.

Two connections with different privileges:

* ``analyst_engine``  - read-only; may select only the sanitized ``vw_*`` views.
  This is what LLM-authored SQL and the secure engine use.
* ``hydrator_engine`` - reads customer contact data to execute *approved*
  actions.  It is used only by the action engine, never by an agent tool.

Local backend: SQLite built from ``data/bank/operational/*.csv`` (analyst opens
it with ``mode=ro``).  GCP backend: Cloud SQL for PostgreSQL through the Cloud
SQL Python Connector with IAM database authentication (no passwords); DB roles
in ``infrastructure/cloudsql/schema.sql`` enforce the same split.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event

from core.config import DATA_DIR, Settings, get_settings

OPERATIONAL_DIR = DATA_DIR / "bank" / "operational"
BOOLEAN_COLUMNS = {"borrower_notified", "has_fixed_rate_option", "has_emi_tenor_choice", "statement_available"}
INTEGER_COLUMNS = {"emi_amount", "outstanding_balance"}

SQLITE_SCHEMA = """
CREATE TABLE branches (branch_id TEXT PRIMARY KEY, branch_name TEXT, city TEXT, region TEXT,
                       manager_name TEXT, manager_email TEXT);
CREATE TABLE customers (customer_id TEXT PRIMARY KEY, customer_ref TEXT UNIQUE NOT NULL, full_name TEXT,
                        email TEXT, phone TEXT, pan TEXT, segment_id TEXT, branch_id TEXT);
CREATE TABLE loans (loan_id TEXT PRIMARY KEY, loan_ref TEXT UNIQUE NOT NULL, customer_id TEXT NOT NULL,
                    product_id TEXT NOT NULL, status TEXT, rate_type TEXT, emi_amount INTEGER,
                    outstanding_balance INTEGER, sanctioned_on TEXT, closed_on TEXT, rate_reset_due TEXT,
                    borrower_notified INTEGER, has_fixed_rate_option INTEGER, has_emi_tenor_choice INTEGER,
                    statement_available INTEGER);
CREATE TABLE loan_events (event_id TEXT PRIMARY KEY, loan_id TEXT NOT NULL, event_type TEXT NOT NULL,
                          occurred_on TEXT NOT NULL);
"""

# Identical view definitions run on SQLite and PostgreSQL.
VIEWS_SQL = """
CREATE VIEW vw_compliance_impact AS
SELECT l.loan_ref, c.customer_ref, l.product_id, c.segment_id, c.branch_id, l.status, l.rate_type,
       l.emi_amount, l.outstanding_balance, l.sanctioned_on, l.closed_on, l.rate_reset_due,
       l.borrower_notified, l.has_fixed_rate_option, l.has_emi_tenor_choice, l.statement_available
FROM loans l JOIN customers c ON c.customer_id = l.customer_id;

CREATE VIEW vw_loan_events AS
SELECT l.loan_ref, c.customer_ref, l.product_id, c.branch_id, c.segment_id, e.event_type, e.occurred_on
FROM loan_events e JOIN loans l ON l.loan_id = e.loan_id JOIN customers c ON c.customer_id = l.customer_id;

CREATE VIEW vw_branch_directory AS
SELECT branch_id, branch_name, city, region FROM branches;
"""

_lock = threading.Lock()


def load_rows(table: str, directory: Path = OPERATIONAL_DIR) -> list[dict]:
    with (directory / f"{table}.csv").open(newline="") as source:
        rows = list(csv.DictReader(source))
    for row in rows:
        for key, value in row.items():
            if key in BOOLEAN_COLUMNS:
                row[key] = value.lower() == "true"
            elif key in INTEGER_COLUMNS:
                row[key] = int(value)
            elif value == "":
                row[key] = None
    return rows


def build_sqlite(path: Path, directory: Path = OPERATIONAL_DIR) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    with sqlite3.connect(tmp) as conn:
        conn.executescript(SQLITE_SCHEMA + VIEWS_SQL)
        for table in ("branches", "customers", "loans", "loan_events"):
            rows = load_rows(table, directory)
            columns = list(rows[0])
            conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                             [tuple(row[c] for c in columns) for row in rows])
    tmp.replace(path)
    return path


def _sqlite_path(settings: Settings) -> Path:
    path = settings.runtime_dir / "operational.db"
    with _lock:
        if not path.exists():
            build_sqlite(path)
    return path


def _cloudsql_engine(settings: Settings, database: str) -> Engine:
    settings.require("cloudsql_instance", "cloudsql_iam_user")
    from google.cloud.sql.connector import Connector, IPTypes

    connector = Connector(ip_type=IPTypes.PRIVATE, refresh_strategy="lazy")

    def connect():
        return connector.connect(settings.cloudsql_instance, "pg8000", user=settings.cloudsql_iam_user,
                                 db=database, enable_iam_auth=True)

    return create_engine("postgresql+pg8000://", creator=connect, pool_size=5, max_overflow=2, pool_pre_ping=True)


@lru_cache(maxsize=4)
def analyst_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    if settings.operational_backend == "gcp":
        engine = _cloudsql_engine(settings, settings.cloudsql_operational_db)

        @event.listens_for(engine, "connect")
        def _read_only(dbapi_connection, _):  # defence in depth on top of the DB role
            cursor = dbapi_connection.cursor()
            cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            cursor.execute("SET statement_timeout = 5000")
            cursor.close()

        return engine
    path = _sqlite_path(settings)
    return create_engine("sqlite://", creator=lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False))


@lru_cache(maxsize=4)
def hydrator_engine(settings: Settings | None = None) -> Engine:
    settings = settings or get_settings()
    if settings.operational_backend == "gcp":
        return _cloudsql_engine(settings, settings.cloudsql_operational_db)
    path = _sqlite_path(settings)
    return create_engine("sqlite://", creator=lambda: sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False))


def reset_engines() -> None:
    analyst_engine.cache_clear()
    hydrator_engine.cache_clear()
