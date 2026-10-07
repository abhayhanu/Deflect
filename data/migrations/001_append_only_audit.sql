ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS check_name TEXT;

CREATE INDEX IF NOT EXISTS audit_log_denied_idx ON audit_log (authorized_by, check_name);

REVOKE ALL ON audit_log FROM PUBLIC;
REVOKE ALL ON audit_log FROM deflect_app;
GRANT SELECT, INSERT ON audit_log TO deflect_app;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO deflect_app;

CREATE OR REPLACE FUNCTION audit_log_is_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'audit_log is append only, % is not allowed', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END $$;

DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log;
CREATE TRIGGER audit_log_append_only
  BEFORE UPDATE OR DELETE ON audit_log
  FOR EACH ROW EXECUTE FUNCTION audit_log_is_append_only();

ALTER TABLE approvals ADD COLUMN IF NOT EXISTS note TEXT;

REVOKE ALL ON approvals FROM deflect_app;
GRANT SELECT, INSERT, UPDATE ON approvals TO deflect_app;

GRANT USAGE, CREATE ON SCHEMA public TO deflect_app;
GRANT SELECT ON seed_meta TO deflect_app;
