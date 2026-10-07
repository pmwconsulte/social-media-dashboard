import os
import psycopg2


# ============================================================
# CONFIGURAÇÃO
# ============================================================

def get_database_url():

    database_url = os.getenv(
        "DATABASE_URL"
    )

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL não está definida."
        )

    return database_url


# ============================================================
# CONEXÃO
# ============================================================

def get_connection():

    database_url = get_database_url()

    try:

        return psycopg2.connect(
            database_url,
            connect_timeout=10,
            sslmode=os.getenv(
                "PGSSLMODE",
                "require"
            ),
        )

    except Exception as erro:

        print(
            f"[DATABASE ERROR] "
            f"{erro}"
        )

        raise


# ============================================================
# TESTE
# ============================================================

def test_connection():

    conn = None

    try:

        conn = get_connection()

        with conn.cursor() as cur:

            cur.execute(
                "SELECT 1"
            )

            return cur.fetchone()[0] == 1

    except Exception as erro:

        print(
            f"[DATABASE TEST ERROR] "
            f"{erro}"
        )

        return False

    finally:

        if conn:
            conn.close()


# ============================================================
# CRIAÇÃO DAS TABELAS
# ============================================================

def create_tables():

    conn = None

    try:

        conn = get_connection()

        with conn.cursor() as cur:

            cur.execute(
                """
                CREATE EXTENSION IF NOT EXISTS pgcrypto;
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (

                    id UUID PRIMARY KEY
                        DEFAULT gen_random_uuid(),

                    name VARCHAR(255)
                        NOT NULL,

                    email VARCHAR(255)
                        UNIQUE NOT NULL,

                    password_hash TEXT,

                    plan_type VARCHAR(50)
                        DEFAULT 'free',

                    created_at TIMESTAMP
                        DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS social_accounts (

                    id UUID PRIMARY KEY
                        DEFAULT gen_random_uuid(),

                    user_id UUID
                        REFERENCES users(id)
                        ON DELETE CASCADE,

                    platform VARCHAR(50)
                        NOT NULL,

                    username VARCHAR(255),

                    access_token TEXT,

                    created_at TIMESTAMP
                        DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_metrics (

                    id UUID PRIMARY KEY
                        DEFAULT gen_random_uuid(),

                    social_account_id UUID
                        REFERENCES social_accounts(id)
                        ON DELETE CASCADE,

                    report_date DATE NOT NULL,

                    followers INTEGER
                        DEFAULT 0,

                    reach INTEGER
                        DEFAULT 0,

                    impressions INTEGER
                        DEFAULT 0,

                    engagement_rate NUMERIC(10,2)
                        DEFAULT 0,

                    profile_views INTEGER
                        DEFAULT 0,

                    website_clicks INTEGER
                        DEFAULT 0,

                    created_at TIMESTAMP
                        DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ai_reports (

                    id UUID PRIMARY KEY
                        DEFAULT gen_random_uuid(),

                    social_account_id UUID
                        REFERENCES social_accounts(id)
                        ON DELETE CASCADE,

                    report_type VARCHAR(50),

                    content TEXT,

                    created_at TIMESTAMP
                        DEFAULT NOW()
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_daily_metrics_date
                ON daily_metrics(report_date);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_daily_metrics_account
                ON daily_metrics(
                    social_account_id
                );
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_social_accounts_platform
                ON social_accounts(platform);
                """
            )

        conn.commit()

        print(
            "[DATABASE] "
            "Tabelas verificadas com sucesso."
        )

        return True

    except Exception as erro:

        if conn:
            conn.rollback()

        print(
            f"[DATABASE ERROR] "
            f"{erro}"
        )

        raise

    finally:

        if conn:
            conn.close()
