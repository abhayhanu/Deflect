CREATE TABLE IF NOT EXISTS customers (
  customer_id  TEXT        PRIMARY KEY,
  full_name    TEXT        NOT NULL,
  email        TEXT        NOT NULL UNIQUE,
  phone        TEXT        NOT NULL,
  city         TEXT        NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  order_id          TEXT          PRIMARY KEY,
  customer_id       TEXT          NOT NULL REFERENCES customers (customer_id),
  status            TEXT          NOT NULL
                    CHECK (status IN ('placed', 'shipped', 'delivered', 'cancelled', 'returned')),
  items             JSONB         NOT NULL,
  total_inr         NUMERIC(10,2) NOT NULL CHECK (total_inr > 0),
  refunded_inr      NUMERIC(10,2) NOT NULL DEFAULT 0 CHECK (refunded_inr >= 0),
  payment_method    TEXT          NOT NULL
                    CHECK (payment_method IN ('upi', 'card', 'netbanking', 'wallet', 'cod')),
  shipping_address  TEXT          NOT NULL,
  shipping_city     TEXT          NOT NULL,
  placed_at         TIMESTAMPTZ   NOT NULL,
  delivered_at      TIMESTAMPTZ,
  cancelled_at      TIMESTAMPTZ,
  CHECK (refunded_inr <= total_inr)
);

CREATE INDEX IF NOT EXISTS orders_customer_idx ON orders (customer_id, placed_at DESC);

CREATE TABLE IF NOT EXISTS shipments (
  shipment_id    TEXT        PRIMARY KEY,
  order_id       TEXT        NOT NULL REFERENCES orders (order_id),
  direction      TEXT        NOT NULL CHECK (direction IN ('forward', 'return')),
  carrier        TEXT        NOT NULL,
  tracking_no    TEXT        NOT NULL UNIQUE,
  status         TEXT        NOT NULL
                 CHECK (status IN ('label_created', 'in_transit', 'out_for_delivery', 'delivered', 'returned_to_origin')),
  shipped_at     TIMESTAMPTZ,
  promised_by    TIMESTAMPTZ,
  last_event     TEXT,
  last_location  TEXT,
  last_event_at  TIMESTAMPTZ,
  delivered_at   TIMESTAMPTZ,
  UNIQUE (order_id, direction)
);

CREATE TABLE IF NOT EXISTS refunds (
  refund_id        TEXT          PRIMARY KEY,
  order_id         TEXT          NOT NULL REFERENCES orders (order_id),
  amount_inr       NUMERIC(10,2) NOT NULL CHECK (amount_inr > 0),
  reason_code      TEXT          NOT NULL
                   CHECK (reason_code IN ('lost_in_transit', 'damaged', 'not_as_described',
                                          'late_delivery', 'goodwill', 'cancellation', 'return')),
  policy_doc_id    TEXT,
  status           TEXT          NOT NULL CHECK (status IN ('pending', 'processed', 'failed')),
  idempotency_key  TEXT          UNIQUE,
  issued_at        TIMESTAMPTZ   NOT NULL
);

CREATE INDEX IF NOT EXISTS refunds_order_idx ON refunds (order_id, issued_at DESC);

CREATE TABLE IF NOT EXISTS approvals (
  approval_id   TEXT        PRIMARY KEY,
  ticket_id     TEXT        NOT NULL,
  thread_id     TEXT        NOT NULL,
  tool_name     TEXT        NOT NULL,
  tool_args     JSONB       NOT NULL,
  reason        TEXT,
  status        TEXT        NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'denied', 'expired')),
  approver_id   TEXT,
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  decided_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS approvals_status_idx ON approvals (status, requested_at);

CREATE TABLE IF NOT EXISTS audit_log (
  id             BIGSERIAL     PRIMARY KEY,
  ticket_id      TEXT          NOT NULL,
  trace_id       TEXT          NOT NULL,
  tool_name      TEXT          NOT NULL,
  tool_args      JSONB         NOT NULL,
  result         JSONB,
  error          TEXT,
  authorized_by  TEXT          NOT NULL CHECK (authorized_by IN ('policy', 'human', 'denied')),
  approver_id    TEXT,
  policy_doc_id  TEXT,
  latency_ms     INT           NOT NULL,
  cost_inr       NUMERIC(10,4),
  created_at     TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_log_ticket_idx ON audit_log (ticket_id);

CREATE UNIQUE INDEX IF NOT EXISTS audit_log_idempotency_uq
  ON audit_log (tool_name, (tool_args->>'idempotency_key'))
  WHERE tool_args ? 'idempotency_key';

CREATE TABLE IF NOT EXISTS seed_meta (
  key         TEXT        PRIMARY KEY,
  value       TEXT        NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
