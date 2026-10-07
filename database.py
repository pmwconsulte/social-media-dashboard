CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    name VARCHAR(255) NOT NULL,

    email VARCHAR(255) UNIQUE NOT NULL,

    password_hash VARCHAR(255),

    plan_type VARCHAR(50) DEFAULT 'free',

    stripe_customer_id VARCHAR(255),

    created_at TIMESTAMP DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS social_accounts (

    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id)
        ON DELETE CASCADE,

    platform VARCHAR(50) NOT NULL,

    platform_account_id VARCHAR(255) NOT NULL,

    username VARCHAR(255),

    access_token TEXT,

    token_expires_at TIMESTAMP,

    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(
        platform,
        platform_account_id
    )
);


CREATE TABLE IF NOT EXISTS daily_metrics (

    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    social_account_id UUID NOT NULL
        REFERENCES social_accounts(id)
        ON DELETE CASCADE,

    date DATE NOT NULL,

    followers INT DEFAULT 0,

    reach INT DEFAULT 0,

    impressions INT DEFAULT 0,

    engagement_rate DECIMAL(8,2) DEFAULT 0,

    profile_views INT DEFAULT 0,

    website_clicks INT DEFAULT 0,

    created_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(
        social_account_id,
        date
    )
);


CREATE TABLE IF NOT EXISTS ai_reports (

    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    social_account_id UUID NOT NULL
        REFERENCES social_accounts(id)
        ON DELETE CASCADE,

    report_type VARCHAR(50)
        DEFAULT 'executive',

    content TEXT NOT NULL,

    period_start DATE,

    period_end DATE,

    created_at TIMESTAMP DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS subscriptions (

    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    user_id UUID NOT NULL
        REFERENCES users(id)
        ON DELETE CASCADE,

    status VARCHAR(50)
        DEFAULT 'active',

    plan_type VARCHAR(50),

    current_period_end TIMESTAMP,

    created_at TIMESTAMP DEFAULT NOW()
);
