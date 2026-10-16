-- Cloud SQL for PostgreSQL: synthetic core-banking extract + sanitized views + least-privilege roles.
-- Applied by scripts/seed_cloudsql.py as the deploying admin (IAM user).
CREATE TABLE IF NOT EXISTS branches (branch_id TEXT PRIMARY KEY, branch_name TEXT, city TEXT, region TEXT,
                                     manager_name TEXT, manager_email TEXT);
CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, customer_ref TEXT UNIQUE NOT NULL, full_name TEXT,
                                      email TEXT, phone TEXT, pan TEXT, segment_id TEXT, branch_id TEXT REFERENCES branches);
CREATE TABLE IF NOT EXISTS loans (loan_id TEXT PRIMARY KEY, loan_ref TEXT UNIQUE NOT NULL,
                                  customer_id TEXT NOT NULL REFERENCES customers, product_id TEXT NOT NULL, status TEXT,
                                  rate_type TEXT, emi_amount BIGINT, outstanding_balance BIGINT, sanctioned_on DATE,
                                  closed_on DATE, rate_reset_due DATE, borrower_notified BOOLEAN,
                                  has_fixed_rate_option BOOLEAN, has_emi_tenor_choice BOOLEAN, statement_available BOOLEAN);
CREATE TABLE IF NOT EXISTS loan_events (event_id TEXT PRIMARY KEY, loan_id TEXT NOT NULL REFERENCES loans,
                                        event_type TEXT NOT NULL, occurred_on DATE NOT NULL);
CREATE INDEX IF NOT EXISTS loans_by_product ON loans (product_id, status);
CREATE INDEX IF NOT EXISTS events_by_loan ON loan_events (loan_id, event_type);

CREATE OR REPLACE VIEW vw_compliance_impact AS
SELECT l.loan_ref, c.customer_ref, l.product_id, c.segment_id, c.branch_id, l.status, l.rate_type,
       l.emi_amount, l.outstanding_balance, l.sanctioned_on, l.closed_on, l.rate_reset_due,
       l.borrower_notified, l.has_fixed_rate_option, l.has_emi_tenor_choice, l.statement_available
FROM loans l JOIN customers c ON c.customer_id = l.customer_id;

CREATE OR REPLACE VIEW vw_loan_events AS
SELECT l.loan_ref, c.customer_ref, l.product_id, c.branch_id, c.segment_id, e.event_type, e.occurred_on
FROM loan_events e JOIN loans l ON l.loan_id = e.loan_id JOIN customers c ON c.customer_id = l.customer_id;

CREATE OR REPLACE VIEW vw_branch_directory AS SELECT branch_id, branch_name, city, region FROM branches;

-- compliance_analyst: what the API (agents) connects as. Views only, read only.
DO $$ BEGIN CREATE ROLE compliance_analyst NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM compliance_analyst;
GRANT USAGE ON SCHEMA public TO compliance_analyst;
GRANT SELECT ON vw_compliance_impact, vw_loan_events, vw_branch_directory TO compliance_analyst;

-- pii_hydrator: only the action-engine service account. Contact columns needed to deliver approved notices.
DO $$ BEGIN CREATE ROLE pii_hydrator NOLOGIN; EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO pii_hydrator;
GRANT SELECT (loan_id, loan_ref, customer_id) ON loans TO pii_hydrator;
GRANT SELECT (customer_id, full_name, email, branch_id) ON customers TO pii_hydrator;
GRANT SELECT (branch_id, branch_name, manager_name, manager_email) ON branches TO pii_hydrator;
