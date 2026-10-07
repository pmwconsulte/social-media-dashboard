import os
from datetime import datetime, timezone


TRIAL_DAYS = 30

PLANS = {
    "trial": {
        "name": "Free Trial",
        "price": "Grátis",
        "accounts": 2,
        "users": 1,
    },
    "starter": {
        "name": "Starter",
        "price": "$9.99/mês",
        "accounts": 3,
        "users": 2,
    },
    "pro": {
        "name": "Pro",
        "price": "$29.99/mês",
        "accounts": 10,
        "users": 5,
    },
    "business": {
        "name": "Business",
        "price": "$79.99/mês",
        "accounts": "Ilimitadas",
        "users": "Ilimitados",
    },
}


def ensure_billing_schema(conn):
    """Ensure the billing table exists without resetting existing subscriptions."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) UNIQUE NOT NULL,
                trial_started_at TIMESTAMP DEFAULT NOW(),
                trial_ends_at TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days'),
                subscription_status VARCHAR(30) DEFAULT 'trialing',
                subscription_plan VARCHAR(50) DEFAULT 'trial',
                payment_provider VARCHAR(50),
                subscription_id VARCHAR(255),
                created_at TIMESTAMP DEFAULT NOW()
            );
            """
        )
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS trial_started_at TIMESTAMP DEFAULT NOW();")
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMP DEFAULT (NOW() + INTERVAL '30 days');")
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS subscription_status VARCHAR(30) DEFAULT 'trialing';")
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS subscription_plan VARCHAR(50) DEFAULT 'trial';")
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS payment_provider VARCHAR(50);")
        cur.execute("ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS subscription_id VARCHAR(255);")
        cur.execute(
            """
            INSERT INTO workspaces (name, slug)
            VALUES ('PMW Default Workspace', 'pmw-default')
            ON CONFLICT (slug) DO NOTHING;
            """
        )
        cur.execute(
            """
            UPDATE workspaces
            SET trial_started_at = COALESCE(trial_started_at, NOW()),
                trial_ends_at = COALESCE(trial_ends_at, NOW() + INTERVAL '30 days'),
                subscription_status = COALESCE(subscription_status, 'trialing'),
                subscription_plan = COALESCE(subscription_plan, 'trial')
            WHERE slug = 'pmw-default';
            """
        )
    conn.commit()


def get_workspace_billing(conn, workspace_slug=None):
    ensure_billing_schema(conn)
    slug = workspace_slug or os.getenv("DEFAULT_WORKSPACE_SLUG", "pmw-default")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                id,
                name,
                slug,
                trial_started_at,
                trial_ends_at,
                subscription_status,
                subscription_plan,
                payment_provider,
                subscription_id
            FROM workspaces
            WHERE slug = %s
            LIMIT 1;
            """,
            (slug,),
        )
        row = cur.fetchone()

    if not row:
        return None

    keys = [
        "id", "name", "slug", "trial_started_at", "trial_ends_at",
        "subscription_status", "subscription_plan", "payment_provider",
        "subscription_id",
    ]
    return dict(zip(keys, row))


def evaluate_workspace_access(conn, workspace_slug=None):
    workspace = get_workspace_billing(conn, workspace_slug)
    if not workspace:
        return {
            "allowed": False,
            "status": "missing",
            "plan": "trial",
            "days_left": 0,
            "workspace": None,
        }

    status = (workspace["subscription_status"] or "trialing").lower()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    trial_end = workspace["trial_ends_at"]

    if status in {"active", "trial", "trialing"}:
        if trial_end and now >= trial_end and status in {"trial", "trialing"}:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE workspaces
                    SET subscription_status = 'expired'
                    WHERE id = %s
                      AND subscription_status IN ('trial', 'trialing');
                    """,
                    (workspace["id"],),
                )
            conn.commit()
            status = "expired"
            workspace["subscription_status"] = "expired"

    allowed = status == "active" or (
        status in {"trial", "trialing"} and
        (not trial_end or now < trial_end)
    )

    days_left = 0
    if trial_end and status in {"trial", "trialing"}:
        seconds = (trial_end - now).total_seconds()
        days_left = max(0, int((seconds + 86399) // 86400))

    return {
        "allowed": allowed,
        "status": status,
        "plan": workspace["subscription_plan"] or "trial",
        "days_left": days_left,
        "workspace": workspace,
    }
