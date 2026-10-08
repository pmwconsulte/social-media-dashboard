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


            cur.execute(
                """ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS display_name VARCHAR(255);"""
            )
            cur.execute(
                """ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS connection_status VARCHAR(30) DEFAULT 'pending';"""
            )
            cur.execute(
                """ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT TRUE;"""
            )
            cur.execute(
                """ALTER TABLE social_accounts ADD COLUMN IF NOT EXISTS last_sync_at TIMESTAMP;"""
            )
            cur.execute(
                """CREATE INDEX IF NOT EXISTS idx_social_accounts_workspace_active ON social_accounts(workspace_id, is_active);"""
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


# ============================================================
# WORKSPACES / MIGRAÇÃO / DADOS INICIAIS
# ============================================================

def initialize_database():
    """
    Inicializa o schema de produção de forma idempotente.
    Também cria um workspace padrão e um dataset inicial apenas
    quando a base ainda não possui contas sociais.
    """
    create_tables()

    conn = None
    try:
        conn = get_connection()
        with conn.cursor() as cur:
            # Workspace base para preparar isolamento por cliente.
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
                SELECT id
                FROM workspaces
                WHERE slug = 'pmw-default'
                LIMIT 1;
                """
            )
            workspace_id = cur.fetchone()[0]

            # Migração segura para instalações existentes.
            cur.execute(
                """
                ALTER TABLE social_accounts
                ADD COLUMN IF NOT EXISTS workspace_id UUID
                REFERENCES workspaces(id)
                ON DELETE CASCADE;
                """
            )

            cur.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS workspace_id UUID
                REFERENCES workspaces(id)
                ON DELETE SET NULL;
                """
            )

            cur.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS role VARCHAR(50)
                DEFAULT 'viewer';
                """
            )

            cur.execute(
                """
                ALTER TABLE users
                ADD COLUMN IF NOT EXISTS is_active BOOLEAN
                DEFAULT TRUE;
                """
            )

            cur.execute(
                """
                UPDATE social_accounts
                SET workspace_id = %s
                WHERE workspace_id IS NULL;
                """,
                (workspace_id,),
            )

            cur.execute(
                """
                UPDATE users
                SET workspace_id = %s
                WHERE workspace_id IS NULL;
                """,
                (workspace_id,),
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_social_accounts_workspace
                ON social_accounts(workspace_id);
                """
            )

            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_users_workspace
                ON users(workspace_id);
                """
            )

            # Evita duplicação de métricas para a mesma conta/data.
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                uq_daily_metrics_account_date
                ON daily_metrics(social_account_id, report_date);
                """
            )

            # Dataset inicial controlado: só entra se a base ainda estiver vazia.
            cur.execute("SELECT COUNT(*) FROM social_accounts;")
            total_accounts = cur.fetchone()[0]

            if total_accounts == 0:
                cur.execute(
                    """
                    INSERT INTO social_accounts
                        (workspace_id, platform, username)
                    VALUES
                        (%s, 'Instagram', '@pmw_demo')
                    RETURNING id;
                    """,
                    (workspace_id,),
                )
                social_account_id = cur.fetchone()[0]

                cur.execute(
                    """
                    INSERT INTO daily_metrics
                        (
                            social_account_id,
                            report_date,
                            followers,
                            reach,
                            impressions,
                            engagement_rate,
                            profile_views,
                            website_clicks
                        )
                    SELECT
                        %s,
                        CURRENT_DATE - gs,
                        1200 + (29 - gs) * 58,
                        9000 + (29 - gs) * 410,
                        14000 + (29 - gs) * 620,
                        ROUND((3.4 + (29 - gs) * 0.10)::numeric, 2),
                        120 + (29 - gs) * 7,
                        18 + (29 - gs) * 2
                    FROM generate_series(0, 29) AS gs;
                    """,
                    (social_account_id,),
                )

                print(
                    "[DATABASE] Dataset inicial criado: "
                    "PMW Default Workspace / @pmw_demo."
                )

        conn.commit()
        print("[DATABASE] Inicialização de produção concluída.")
        return True

    except Exception as erro:
        if conn:
            conn.rollback()
        print(f"[DATABASE ERROR] Falha na inicialização: {erro}")
        traceback = __import__("traceback")
        traceback.print_exc()
        return False
    finally:
        if conn:
            conn.close()


# Executado também quando o serviço é iniciado por Gunicorn.
# O bloco if __name__ == '__main__' do app.py não é executado pelo Gunicorn.
try:
    initialize_database()
except Exception as erro:
    print(f"[DATABASE WARNING] Inicialização automática ignorada: {erro}")
