"""
Скрипт инициализации базы данных Supabase
Запусти: python scripts/setup_database.py
Скопируй выведенный SQL в Supabase Dashboard → SQL Editor → Run
"""

CREATE_TABLES_SQL = """
-- Таблица заказов
CREATE TABLE IF NOT EXISTS orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    username TEXT,
    description TEXT,
    status TEXT DEFAULT 'awaiting_payment',
    prompt TEXT,
    delivery_admin_id BIGINT,  -- какой админ сейчас доставляет (NULL = никто)
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица сообщений (история переписки для контекста Claude)
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
-- Миграция для существующих баз данных (v0.1.0 → v0.2.0)
-- Добавляет колонку delivery_admin_id если её ещё нет
ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_admin_id BIGINT;
"""

if __name__ == "__main__":
    print("=" * 60)
    print("Скопируй SQL ниже и выполни в Supabase Dashboard:")
    print("https://app.supabase.com → твой проект → SQL Editor")
    print("=" * 60)
    print("-- === НОВАЯ УСТАНОВКА (первый запуск) ===")
    print(CREATE_TABLES_SQL)
    print()
    print("-- === МИГРАЦИЯ (если БД уже была создана) ===")
    print(MIGRATE_SQL)
    print("=" * 60)
    print("Готово! После выполнения SQL можно запускать бота.")
