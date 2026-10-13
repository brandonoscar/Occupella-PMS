"""Property-based tests for documents (migration 20261012000036).

Hypothesis has the app (trust_app), naming a staff member or nobody, upload documents of every
kind about records of its PMC or of another PMC, with files that are what their row says or
aren't (another type's first bytes, a size or SHA-256 that doesn't match), and remove them, some
twice and some under another PMC. A model predicts what the database keeps; it must agree on
every step, refusing what it refuses for the reason it gives.

Invariants checked after every step (ci/registry.toml maps them to this test):
  - the PMC's documents are exactly the model's: kind, record, size, SHA-256 and who uploaded;
  - every document has its one file, and the file is the bytes it was uploaded with;
  - removals are exactly the model's, each by whoever the app named;
  - the audit log holds one insert per document and per removal, and never a file's bytes.

Run more examples locally with HYPOTHESIS_PROFILE=nightly.
"""

import hashlib
from dataclasses import dataclass
from datetime import date

import psycopg
from helpers import JPEG, PDF, PNG, acting_as, add_staff, add_unit, enter_bill, make_pmc, open_lease
from hypothesis import event
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

KINDS = ("w9", "receipt", "move_out_photo", "owner_document")
COLUMN = {
    "w9": "vendor_id",
    "receipt": "bill_id",
    "move_out_photo": "lease_id",
    "owner_document": "owner_id",
}
MAGIC = {"application/pdf": PDF[:5], "image/png": PNG[:8], "image/jpeg": JPEG[:3]}
FLAVOURS = ("good", "good", "good", "another type", "wrong size", "wrong digest")
STAFF = 2
WHO = st.one_of(st.none(), st.integers(min_value=0, max_value=STAFF - 1))


@dataclass
class Kept:
    id: object
    kind: str
    about: object
    content: bytes
    by: object
    removed_by: object = None
    removed: bool = False


def records(conn, pmc):
    """One record of the PMC for each kind of document to be about."""
    bill = enter_bill(
        conn,
        pmc.pmc_id,
        pmc.vendor_id,
        pmc.owners[0].account,
        "INV-1",
        date(2026, 1, 5),
        date(2026, 2, 1),
        "100.00",
    )
    tenant = conn.execute(
        "SELECT tenant_id FROM trust_ledger_accounts WHERE ledger_account_id = %s",
        (pmc.tenant_deposit,),
    ).fetchone()[0]
    unit = add_unit(conn, pmc.pmc_id, pmc.owners[0].property_id)
    lease = open_lease(conn, pmc.pmc_id, unit, date(2026, 1, 1), None, "1000.00", [tenant])
    return {
        "w9": pmc.vendor_id,
        "receipt": bill,
        "move_out_photo": lease,
        "owner_document": pmc.owners[0].owner_id,
    }


class DocumentsMachine(RuleBasedStateMachine):
    def __init__(self, conn, app_conn):
        super().__init__()
        self.conn, self.app = conn, app_conn
        self.pmc = make_pmc(conn)
        other = make_pmc(conn)
        self.mine, self.theirs = records(conn, self.pmc), records(conn, other)
        self.other_pmc = other.pmc_id
        self.staff = [add_staff(conn, self.pmc.pmc_id, f"Clerk {n + 1}") for n in range(STAFF)]
        self.kept: list[Kept] = []

    def named(self, who):
        return None if who is None else self.staff[who]

    @rule(
        kind=st.sampled_from(KINDS),
        ours=st.booleans(),
        content_type=st.sampled_from(sorted(MAGIC)),
        flavour=st.sampled_from(FLAVOURS),
        rest=st.binary(min_size=11, max_size=64),
        who=WHO,
    )
    def upload(self, kind, ours, content_type, flavour, rest, who):
        if flavour == "another type":
            other_type = next(t for t in sorted(MAGIC) if t != content_type)
            content = MAGIC[other_type] + rest
        else:
            content = MAGIC[content_type] + rest
        size = len(content) + (1 if flavour == "wrong size" else 0)
        digest = hashlib.sha256(content + (b"!" if flavour == "wrong digest" else b"")).digest()
        about = (self.mine if ours else self.theirs)[kind]

        def send():
            with self.app.transaction():
                document = self.app.execute(
                    f"INSERT INTO trust_documents (pmc_id, kind, {COLUMN[kind]}, title,"
                    " content_type, size, sha256) VALUES (%s, %s, %s, 'Scan', %s, %s, %s)"
                    " RETURNING id",
                    (self.pmc.pmc_id, kind, about, content_type, size, digest),
                ).fetchone()[0]
                self.app.execute(
                    "INSERT INTO trust_document_files (document_id, pmc_id, content)"
                    " VALUES (%s, %s, %s)",
                    (document, self.pmc.pmc_id, content),
                )
            return document

        with acting_as(self.app, self.named(who)):
            if not ours:
                event("upload: refused, another PMC's record")
                try:
                    send()
                except psycopg.errors.ForeignKeyViolation:
                    return
                raise AssertionError("a document about another PMC's record was kept")
            if flavour != "good":
                event(f"upload: refused, {flavour}")
                try:
                    send()
                except psycopg.errors.CheckViolation as refused:
                    assert "is not" in str(refused), refused
                    return
                raise AssertionError(f"a file with {flavour} was kept")
            event(f"upload: {kind}")
            self.kept.append(Kept(send(), kind, about, content, self.named(who)))

    @precondition(lambda self: self.kept)
    @rule(data=st.data(), who=WHO, under_other_pmc=st.booleans())
    def remove(self, data, who, under_other_pmc):
        kept = data.draw(st.sampled_from(self.kept))
        pmc = self.other_pmc if under_other_pmc else self.pmc.pmc_id
        with acting_as(self.app, self.named(who)):
            try:
                with self.app.transaction():
                    self.app.execute(
                        "INSERT INTO trust_document_removals (document_id, pmc_id) VALUES (%s, %s)",
                        (kept.id, pmc),
                    )
            except psycopg.errors.ForeignKeyViolation:
                assert under_other_pmc and not kept.removed
                event("remove: refused, another PMC")
                return
            except psycopg.errors.UniqueViolation:
                # Removed already: Postgres checks that before whose document it is.
                assert kept.removed
                event("remove: refused, removed already")
                return
        assert not under_other_pmc and not kept.removed
        event("remove")
        kept.removed, kept.removed_by = True, self.named(who)

    @invariant()
    def documents_are_the_models(self):
        rows = self.conn.execute(
            "SELECT d.id, d.kind, coalesce(d.vendor_id, d.bill_id, d.lease_id, d.owner_id), d.size,"
            " d.sha256, d.uploaded_by, f.content FROM trust_documents AS d"
            " JOIN trust_document_files AS f ON f.document_id = d.id AND f.pmc_id = d.pmc_id"
            " WHERE d.pmc_id = %s ORDER BY d.id",
            (self.pmc.pmc_id,),
        ).fetchall()
        expected = sorted(
            (
                k.id,
                k.kind,
                k.about,
                len(k.content),
                hashlib.sha256(k.content).digest(),
                k.by,
                k.content,
            )
            for k in self.kept
        )
        assert [(r[0], r[1], r[2], r[3], bytes(r[4]), r[5], bytes(r[6])) for r in rows] == expected
        # The join above drops a document without its file; none may exist.
        documents = self.conn.execute(
            "SELECT count(*) FROM trust_documents WHERE pmc_id = %s", (self.pmc.pmc_id,)
        ).fetchone()[0]
        assert documents == len(self.kept)

    @invariant()
    def removals_are_the_models(self):
        rows = self.conn.execute(
            "SELECT document_id, removed_by FROM trust_document_removals WHERE pmc_id = %s"
            " ORDER BY document_id",
            (self.pmc.pmc_id,),
        ).fetchall()
        assert rows == sorted((k.id, k.removed_by) for k in self.kept if k.removed)

    @invariant()
    def the_log_has_each_document_and_never_its_bytes(self):
        logged = self.conn.execute(
            "SELECT table_name, count(*) FROM trust_audit_log WHERE pmc_id = %s"
            " AND table_name LIKE 'trust_document%%' GROUP BY table_name ORDER BY table_name",
            (self.pmc.pmc_id,),
        ).fetchall()
        expected = [
            ("trust_document_removals", sum(k.removed for k in self.kept)),
            ("trust_documents", len(self.kept)),
        ]
        assert logged == [row for row in expected if row[1]]
        for kept in self.kept:
            assert not self.conn.execute(
                "SELECT 1 FROM trust_audit_log WHERE pmc_id = %s AND row_data::text LIKE %s",
                (self.pmc.pmc_id, f"%{kept.content.hex()}%"),
            ).fetchone()


def test_documents_are_their_files_and_their_bytes_stay_out_of_the_log(database_url):
    with (
        psycopg.connect(database_url, autocommit=True) as conn,
        psycopg.connect(database_url, autocommit=True) as app_conn,
    ):
        app_conn.execute("SET ROLE trust_app")
        run_state_machine_as_test(lambda: DocumentsMachine(conn, app_conn))
