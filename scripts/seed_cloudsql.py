"""Create the Cloud SQL schema, load the synthetic bank, and grant least-privilege roles.

Runs as the Cloud Run job ``yukdhis-seed`` inside the VPC (see deploy.sh seed).
Env: CLOUDSQL_INSTANCE, CLOUDSQL_ADMIN_PASSWORD (Secret Manager), API_DB_USER, ACTION_DB_USER.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bank_mcp_server.operational_store import load_rows  # noqa: E402

TABLES = ("branches", "customers", "loans", "loan_events")


def connect(database: str):
    from google.cloud.sql.connector import Connector, IPTypes

    return Connector(ip_type=IPTypes.PRIVATE).connect(
        os.environ["CLOUDSQL_INSTANCE"], "pg8000", user="postgres", password=os.environ["CLOUDSQL_ADMIN_PASSWORD"], db=database)


def quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def main() -> None:
    api_user, action_user = quote(os.environ["API_DB_USER"]), quote(os.environ["ACTION_DB_USER"])
    conn = connect("bank_ops")
    cur = conn.cursor()
    for statement in (ROOT / "infrastructure" / "cloudsql" / "schema.sql").read_text().split(";\n"):
        sql = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).strip()
        if sql:
            cur.execute(sql)
    cur.execute("TRUNCATE loan_events, loans, customers, branches")
    for table in TABLES:
        rows = load_rows(table)
        columns = list(rows[0])
        cur.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))})",
                        [tuple(row[c] for c in columns) for row in rows])
        print(f"loaded {len(rows)} rows into {table}")
    for view in ("vw_compliance_impact", "vw_asset_quality", "vw_kyc_status"):  # the sanitized views the analyst role reads
        cur.execute(f"SELECT COUNT(*) FROM {view}")
        print(f"{view}: {cur.fetchone()[0]} rows")
    cur.execute(f"GRANT compliance_analyst TO {api_user}")
    cur.execute(f"GRANT pii_hydrator TO {action_user}")
    conn.commit()
    conn.close()

    app = connect("yukdhis_app")
    cur = app.cursor()
    for user in (api_user, action_user):
        cur.execute(f"GRANT ALL ON SCHEMA public TO {user}")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO {user}")
        cur.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO {user}")
    app.commit()
    app.close()
    print("Cloud SQL seeded; analyst and hydrator roles granted.")


if __name__ == "__main__":
    main()
