import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():

    return psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor
    )


def create_tables():

    conn = get_connection()

    cur = conn.cursor()

    cur.execute("""

    CREATE TABLE IF NOT EXISTS users (

        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

        name VARCHAR(255) NOT NULL,

        email VARCHAR(255) UNIQUE NOT NULL,

        password_hash TEXT,

        plan_type VARCHAR(50) DEFAULT 'free',

        created_at TIMESTAMP DEFAULT NOW()

    )

    """)

    cur.execute("""

    CREATE TABLE IF NOT EXISTS social_accounts (

        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

        user_id UUID REFERENCES users(id),

        platform VARCHAR(50),

        username VARCHAR(255),

        access_token TEXT,

        created_at TIMESTAMP DEFAULT NOW()

    )

    """)

    cur.execute("""

    CREATE TABLE IF NOT EXISTS daily_metrics (

        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

        social_account_id UUID REFERENCES social_accounts(id),

        report_date DATE,

        followers INTEGER DEFAULT 0,

        reach INTEGER DEFAULT 0,

        impressions INTEGER DEFAULT 0,

        engagement_rate NUMERIC(10,2) DEFAULT 0,

        profile_views INTEGER DEFAULT 0,

        website_clicks INTEGER DEFAULT 0,

        created_at TIMESTAMP DEFAULT NOW()

    )

    """)

    cur.execute("""

    CREATE TABLE IF NOT EXISTS ai_reports (

        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

        social_account_id UUID REFERENCES social_accounts(id),

        report_type VARCHAR(50),

        content TEXT,

        created_at TIMESTAMP DEFAULT NOW()

    )

    """)

    conn.commit()

    cur.close()

    conn.close()

