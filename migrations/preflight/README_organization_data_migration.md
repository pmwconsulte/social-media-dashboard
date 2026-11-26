# Runbook — PMW Consultoria organization migration preparation

Status: PREPARATION ONLY. Nothing in this runbook has been executed against PostgreSQL.
Target branch: `feature/multi-organization-foundation`.

## Objective

Group the three existing workspaces under one organization named **PMW Consultoria**, while retaining each workspace as a separate data and access boundary. Preserve all existing users, workspace UUIDs, social accounts, tokens, messages, metrics, OAuth state, PayPal history, and subscription reference.

## Explicit safety boundaries

- Do not run any migration against production during this preparation.
- Do not modify Render environment variables, production tokens, Meta permissions, PayPal subscriptions, or user credentials.
- Do not delete, merge, rename, or recreate existing workspaces or users.
- Do not move social accounts or historical records between workspaces.
- Do not clear workspace-level billing fields during the compatibility period.
- Do not grant users membership in workspaces they did not previously belong to.
- Do not automatically change legacy `users.role` values.

## Migration order

1. Review and apply `migrations/20261126_001_organization_foundation.sql` in a disposable/development PostgreSQL database.
2. Review and apply `migrations/20261126_002_organization_memberships.sql` in the same development database.
3. Review and apply `migrations/20261126_003_primary_organization_owner.sql`.
4. Run `migrations/preflight/20261126_organization_migration_preflight.sql` and save the complete result sets.
5. Reconcile every preflight result against the expected identities below.
6. Only after a clean preflight, prepare a separate, explicit, transactional data migration. Do not improvise UPDATE statements interactively.

## Expected organization

- Name: `PMW Consultoria`
- Slug: `pmw-consultoria`
- One organization subscription, copied from the existing paid Starter workspace as a compatibility-preserving migration.
- The workspace-level billing fields remain unchanged until organization billing is implemented, tested, and deliberately switched over. The subscription is not recreated, cancelled, or modified at the payment provider.

## Existing workspaces — preserve exact identities

| Workspace | UUID | Existing slug |
|---|---|---|
| PMW Consultoria Workspace | `be43b2c9-4bbc-40a8-8f46-c67b53e2ace7` | `pmw-consultoria-98fdcf3f` |
| PMW Consult Workspace | `746181b5-a798-4f0e-805e-b379d713ca9c` | `pmw-consult-02778e56` |
| PMW Default Workspace | `9e28c16c-63af-458b-b403-4f09d34df8d9` | `pmw-default` |

All three should receive the same `organization_id`, but must remain separate workspaces with their original IDs and slugs.

## Organization owners and workspace access

| User | UUID | Intended organization role | Primary |
|---|---|---|---|
| PMW Consultoria & Tecnologia | `96a8116f-2ebc-4aa9-b253-cc5a1fb49428` | `owner` | Yes |
| PMW Consultoria | `531620c9-05ea-4546-83bd-f174420ea871` | `owner` | No |

Both existing Owners retain the Owner-level organization access. The primary-owner flag distinguishes the designated primary Owner; it does not demote the second Owner.

For workspace membership, only create membership rows that reproduce each user's verified existing workspace access. Current audit showed both users belong to PMW Consultoria Workspace. Do not grant either user membership in the other two workspaces as part of this migration unless a fresh, approved access review establishes that they already had such access.

Legacy `users.role` values remain unchanged. Workspace roles remain separate from organization roles.

## Subscription preservation

The current paid Starter subscription is attached to PMW Consultoria Workspace, with PayPal as provider. The data migration must:

1. Read the existing billing fields from that exact workspace row.
2. Assert exactly one matching active Starter PayPal subscription is present.
3. Copy its status, plan, provider, subscription reference, and trial dates to the organization.
4. Leave the original workspace billing fields untouched as a compatibility mirror.
5. Never call PayPal APIs or create, cancel, activate, or update a subscription.

The actual subscription reference must not be printed in preflight output or committed to source control.

## Required preflight acceptance checks

- All three expected workspace UUIDs exist and their slugs match.
- Both expected user UUIDs exist, remain active, and still have legacy role `owner`.
- The two users' current workspace associations match the approved access baseline.
- The existing Starter PayPal subscription is attached to exactly one workspace.
- No workspace is already assigned to a different organization.
- No unexpected organization with slug `pmw-consultoria` exists.
- Migrations 001–003 are present and schema constraints are valid.
- Record counts by workspace are consistent with the baseline audit; do not compare or print tokens or message contents.

Any failed or ambiguous check blocks the data migration. Resolve the discrepancy and rerun preflight; do not force the migration.

## Rollback strategy

- Before data seeding: migrations 003, 002, then 001 can be rolled back in reverse order, subject to their guards.
- After organization/workspace/user membership data exists: use a separately reviewed data rollback plan. Do not use the structural rollback scripts to delete populated organization records.
- Do not drop workspaces, users, social accounts, messages, metrics, snapshots, or payment history.

## Current state

This is a runbook and read-only preflight preparation only. The organization has not been created, no workspace has been assigned, no membership has been inserted, and no production database has been accessed or changed.
