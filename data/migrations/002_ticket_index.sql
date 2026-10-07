CREATE TABLE IF NOT EXISTS tickets (
  ticket_id        TEXT          PRIMARY KEY,
  channel          TEXT          NOT NULL,
  customer_id      TEXT,
  preview          TEXT          NOT NULL,
  status           TEXT          NOT NULL
                   CHECK (status IN ('done', 'awaiting_approval', 'stopped')),
  intent           TEXT,
  decision         TEXT,
  terminal_reason  TEXT,
  cost_inr         NUMERIC(10,4) NOT NULL DEFAULT 0,
  latency_ms       INT,
  source           TEXT          NOT NULL DEFAULT 'api',
  created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tickets_created_idx ON tickets (created_at DESC);

REVOKE ALL ON tickets FROM deflect_app;
GRANT SELECT, INSERT, UPDATE ON tickets TO deflect_app;
