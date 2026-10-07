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


def get_workspace_billing(conn, workspace_slug=None):
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
