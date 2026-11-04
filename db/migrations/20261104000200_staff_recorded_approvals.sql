-- migrate:up
-- An owner's approval that staff record names who (the founder's review of 11/03, item 2): the
-- staff member from the session (`recorded_by`, set by trust_name_recorder from trust.staff_id),
-- the owner by id (the owner of the bill's property account, as trust_check_bill_decision
-- already holds any approval naming one to), how the owner approved (phone, email or in person;
-- carried_over for an approval brought from the old system with its bill, src/pms/api/switching.py)
-- and a note. An approval a named staff member records without all three is refused; the owner's
-- own approvals in the portal name no staff member and need none.

-- On a live database, give up rather than queue behind a long transaction while holding locks.
SET LOCAL lock_timeout = '5s';
SET LOCAL statement_timeout = '60s';

ALTER TABLE trust_bill_approvals ADD COLUMN recorded_by uuid;
ALTER TABLE trust_bill_approvals ADD COLUMN method text;
ALTER TABLE trust_bill_approvals ADD COLUMN note text;
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_recorder
    FOREIGN KEY (pmc_id, recorded_by) REFERENCES trust_staff (pmc_id, id) NOT VALID;
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_method
    CHECK (method IS NULL OR method IN ('phone', 'email', 'in_person', 'carried_over')) NOT VALID;
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_note
    CHECK (note IS NULL OR (note ~ '\S' AND length(note) <= 1000)) NOT VALID;
ALTER TABLE trust_bill_approvals ADD CONSTRAINT trust_bill_approvals_recorded_whole
    CHECK (recorded_by IS NULL OR (owner_id IS NOT NULL AND method IS NOT NULL AND note IS NOT NULL))
    NOT VALID;
-- Validated in the same transaction (dbmate runs each migration in one): the new columns are
-- null in every row, so each reads the table once, under the 5 s lock timeout above.
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_approvals VALIDATE CONSTRAINT trust_bill_approvals_recorder;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_approvals VALIDATE CONSTRAINT trust_bill_approvals_method;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_approvals VALIDATE CONSTRAINT trust_bill_approvals_note;
-- squawk-ignore constraint-missing-not-valid
ALTER TABLE trust_bill_approvals VALIDATE CONSTRAINT trust_bill_approvals_recorded_whole;
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX trust_bill_approvals_recorded_by ON trust_bill_approvals (pmc_id, recorded_by);

CREATE TRIGGER trust_recorded_by BEFORE INSERT ON trust_bill_approvals
FOR EACH ROW EXECUTE FUNCTION trust_name_recorder();

-- migrate:down
SET LOCAL lock_timeout = '5s';
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM trust_bill_approvals WHERE recorded_by IS NOT NULL) THEN
        RAISE EXCEPTION 'refusing to undo recorded approvals: staff have recorded some';
    END IF;
END;
$$;
DROP TRIGGER trust_recorded_by ON trust_bill_approvals;
-- squawk-ignore require-concurrent-index-deletion
DROP INDEX trust_bill_approvals_recorded_by;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_approvals DROP CONSTRAINT trust_bill_approvals_recorded_whole;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_approvals DROP CONSTRAINT trust_bill_approvals_note;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_approvals DROP CONSTRAINT trust_bill_approvals_method;
-- squawk-ignore ban-drop-constraint
ALTER TABLE trust_bill_approvals DROP CONSTRAINT trust_bill_approvals_recorder;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_bill_approvals DROP COLUMN note;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_bill_approvals DROP COLUMN method;
-- squawk-ignore ban-drop-column
ALTER TABLE trust_bill_approvals DROP COLUMN recorded_by;
