"""Supabase database initialization script.

Run: python scripts/setup_database.py
Copy the printed SQL into Supabase Dashboard → SQL Editor → Run.
"""

CREATE_TABLES_SQL = """
-- Orders table
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT,
    description TEXT,
    status TEXT DEFAULT 'awaiting_payment',
    prompt TEXT,
    delivery_admin_id BIGINT,  -- administrator currently delivering (NULL = none)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Messages table (conversation history for Claude context)
CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_messages_order_id ON messages(order_id);
"""

MIGRATE_SQL = """
-- Migration for existing databases (v0.1.0 → v0.2.0)
-- Adds the delivery_admin_id column if it does not exist yet
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_admin_id BIGINT;
"""

if __name__ == "__main__":
    print("=" * 60)
    print("Copy the SQL below and execute it in Supabase Dashboard:")
    print("https://app.supabase.com → your project → SQL Editor")
    print("=" * 60)
    print("-- === NEW INSTALLATION (first run) ===")
    print(CREATE_TABLES_SQL)
    print()
    print("-- === MIGRATION (if the database already exists) ===")
    print(MIGRATE_SQL)
    print("=" * 60)
    print("Done! You can start the bot after executing the SQL.")
