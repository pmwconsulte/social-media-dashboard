-- PMW multi-organization foundation
-- Version: 20261126_001
-- Apply manually in a reviewed maintenance window; never run automatically at app startup.
-- This migration is intentionally structural: it does not create organizations,
-- assign existing workspaces, or move any subscription/payment data.

BEGIN;

CREATE TABLE IF NOT EXISTS organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(255) NOT NULL UNIQUE,
    subscription_status VARCHAR(30) NOT NULL DEFAULT 'trialing',
    subscription_plan VARCHAR(50) NOT NULL DEFAULT 'trial',
    payment_provider VARCHAR(50),
    subscription_id VARCHAR(255),
    trial_started_at TIMESTAMP,
    trial_ends_at TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE workspaces
    ADD COLUMN IF NOT EXISTS organization_id UUID;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'workspaces_organization_id_fkey'
          AND conrelid = 'public.workspaces'::regclass
    ) THEN
        ALTER TABLE workspaces
            ADD CONSTRAINT workspaces_organization_id_fkey
            FOREIGN KEY (organization_id)
            REFERENCES organizations(id)
            ON DELETE RESTRICT
            NOT VALID;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_workspaces_organization_id
    ON workspaces(organization_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_organizations_payment_subscription
    ON organizations(payment_provider, subscription_id)
    WHERE payment_provider IS NOT NULL
      AND subscription_id IS NOT NULL;

COMMIT;
