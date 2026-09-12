-- Broker capability / execution rejection decision types (UI lifecycle semantics).

BEGIN;

ALTER TYPE decision_type ADD VALUE IF NOT EXISTS 'broker_capability_denied';
ALTER TYPE decision_type ADD VALUE IF NOT EXISTS 'broker_rejected';

COMMIT;
