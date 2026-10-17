"""App-state tables created by one service identity must be shared with the other (Postgres ownership)."""

from __future__ import annotations

from sqlalchemy import create_engine

from core.app_store import share_owned_objects
from core.config import split_list


def test_grantees_parsing():
    assert split_list("api@p.iam; actions@p.iam,  ") == ("api@p.iam", "actions@p.iam")
    assert split_list(None) == () and split_list("") == ()


def test_sqlite_is_a_no_op():
    assert share_owned_objects(create_engine("sqlite://"), ("someone",)) == []


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def scalar_one(self):
        return self.rows

    def all(self):
        return self.rows


class _FakeConn:
    def __init__(self, log, fail_on):
        self.log, self.fail_on = log, fail_on

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement):
        sql = str(statement)
        if sql == "SELECT current_user":
            return _FakeResult("api@p.iam")
        if sql.startswith("SELECT c.relname"):
            return _FakeResult([("conversations", "r"), ("chat_messages_id_seq", "S")])
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("role does not exist")
        self.log.append(sql)
        return _FakeResult(None)


class _FakeEngine:
    class dialect:  # noqa: N801 - mimics SQLAlchemy's attribute
        name = "postgresql"

    def __init__(self, fail_on=None):
        self.log: list[str] = []
        self.fail_on = fail_on

    def connect(self):
        return _FakeConn(self.log, self.fail_on)

    begin = connect


def test_owned_objects_granted_to_others_but_not_self():
    engine = _FakeEngine()
    granted = share_owned_objects(engine, ("api@p.iam", 'act"ions@p.iam'))
    assert granted == ['conversations->act"ions@p.iam', 'chat_messages_id_seq->act"ions@p.iam']
    assert engine.log == ['GRANT ALL ON TABLE "conversations" TO "act""ions@p.iam"',
                          'GRANT ALL ON SEQUENCE "chat_messages_id_seq" TO "act""ions@p.iam"']


def test_grant_failure_does_not_raise():
    engine = _FakeEngine(fail_on="missing@p.iam")
    assert share_owned_objects(engine, ("missing@p.iam",)) == []
