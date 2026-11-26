-- Rollback for 20261126_003_primary_organization_owner.sql
-- Safe only while no primary-owner membership has been designated.

BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM organization_memberships
        WHERE is_primary_owner = TRUE
    ) THEN
        RAISE EXCEPTION
            'Rollback blocked: a primary organization owner is designated. Reassign/clear it explicitly first.';
    END IF;
END
$$;

DROP INDEX IF EXISTS uq_organization_single_primary_owner;

ALTER TABLE organization_memberships
    DROP COLUMN IF EXISTS is_primary_owner;

COMMIT;
