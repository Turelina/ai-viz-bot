# Схема Базы Данных

> Supabase PostgreSQL Schema для мульти-агентной системы

## Обзор

Система использует 7 таблиц для полного отслеживания заказов, сообщений, оплат и токенов.

---

## 1. users (Пользователи)

```sql
CREATE TABLE users (
    id BIGSERIAL PRIMARY KEY,
    telegram_id BIGINT UNIQUE NOT NULL,
    username VARCHAR(255),
    first_name VARCHAR(255),
    last_name VARCHAR(255),
    phone VARCHAR(50),

    -- Статистика
    total_orders INT DEFAULT 0,
    total_spent DECIMAL(10, 2) DEFAULT 0,

    -- Настройки
    language VARCHAR(5) DEFAULT 'ru',
    notifications_enabled BOOLEAN DEFAULT true,

    -- Временные метки
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_activity TIMESTAMP
);

CREATE INDEX idx_users_telegram_id ON users(telegram_id);
```

**Описание**: Хранит информацию о клиентах

---

## 2. orders (Заказы)

```sql
CREATE TYPE order_state AS ENUM (
    'new',
    'collecting_requirements',
    'awaiting_payment',
    'payment_verification',
    'creating_prompt',
    'ready_for_generation',
    'generating',
    'quality_check',
    'delivering',
    'completed',
    'cancelled'
);

CREATE TABLE orders (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,

    -- Состояние
    state order_state DEFAULT 'new',

    -- Детали заказа
    requirements TEXT,
    requirements_summary TEXT,
    complexity VARCHAR(20), -- simple, medium, complex

    -- Промпт
    prompt_ru TEXT,
    prompt_en TEXT,
    generation_platform VARCHAR(50), -- imagen3, dalle3, midjourney
    generation_parameters JSONB,

    -- Цена
    price DECIMAL(10, 2),
    price_multiplier DECIMAL(3, 2) DEFAULT 1.0,

    -- Файлы
    result_file_url TEXT,
    result_file_path TEXT,

    -- Статистика токенов
    total_tokens_used INT DEFAULT 0,
    total_cost_usd DECIMAL(10, 4) DEFAULT 0,

    -- Временные метки
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,

    -- Дедлайны
    payment_deadline TIMESTAMP,
    delivery_deadline TIMESTAMP
);

CREATE INDEX idx_orders_user_id ON orders(user_id);
CREATE INDEX idx_orders_state ON orders(state);
CREATE INDEX idx_orders_created_at ON orders(created_at);
```

**Описание**: Основная таблица заказов со всеми деталями

---

## 3. messages (Сообщения)

```sql
CREATE TABLE messages (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,

    -- Отправитель
    sender_type VARCHAR(20), -- client, agent
    sender_name VARCHAR(100), -- listener, manager, vision, etc.

    -- Содержимое
    content TEXT NOT NULL,
    message_type VARCHAR(50), -- text, image, file

    -- AI использование
    agent_used VARCHAR(50),
    tokens_used INT DEFAULT 0,
    model_used VARCHAR(100),

    -- Временная метка
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_messages_order_id ON messages(order_id);
CREATE INDEX idx_messages_user_id ON messages(user_id);
CREATE INDEX idx_messages_created_at ON messages(created_at);
```

**Описание**: История всех сообщений и взаимодействий

---

## 4. payments (Оплаты)

```sql
CREATE TYPE payment_status AS ENUM (
    'pending',
    'verifying',
    'confirmed',
    'rejected',
    'refunded'
);

CREATE TABLE payments (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES users(id) ON DELETE CASCADE,

    -- Сумма
    amount DECIMAL(10, 2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'RUB',

    -- Статус
    status payment_status DEFAULT 'pending',

    -- Скриншот чека
    screenshot_url TEXT,
    screenshot_path TEXT,

    -- Результат проверки Vision
    vision_confidence DECIMAL(3, 2),
    vision_result JSONB,
    verified_by VARCHAR(50), -- auto, manual

    -- Временные метки
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    verified_at TIMESTAMP
);

CREATE INDEX idx_payments_order_id ON payments(order_id);
CREATE INDEX idx_payments_status ON payments(status);
```

**Описание**: Отслеживание оплат и проверка чеков

---

## 5. generation_jobs (Задачи Генерации)

```sql
CREATE TYPE generation_status AS ENUM (
    'pending',
    'instructions_sent',
    'in_progress',
    'completed',
    'failed'
);

CREATE TABLE generation_jobs (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,

    -- Платформа и промпт
    platform VARCHAR(50), -- imagen3, dalle3, midjourney
    prompt TEXT NOT NULL,
    parameters JSONB,

    -- Статус
    status generation_status DEFAULT 'pending',

    -- Результат
    result_url TEXT,
    result_path TEXT,

    -- Метаданные
    generation_time_seconds INT,
    operator_id BIGINT, -- кто генерировал (в ручном режиме)

    -- Временные метки
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX idx_generation_jobs_order_id ON generation_jobs(order_id);
CREATE INDEX idx_generation_jobs_status ON generation_jobs(status);
```

**Описание**: Управление задачами генерации изображений

---

## 6. state_transitions (Переходы Состояний)

```sql
CREATE TABLE state_transitions (
    id BIGSERIAL PRIMARY KEY,
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,

    -- Переход
    from_state VARCHAR(50),
    to_state VARCHAR(50),

    -- Инициатор
    triggered_by VARCHAR(50), -- agent_name, system, user

    -- Детали
    reason TEXT,
    metadata JSONB,

    -- Временная метка
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_state_transitions_order_id ON state_transitions(order_id);
CREATE INDEX idx_state_transitions_created_at ON state_transitions(created_at);
```

**Описание**: Журнал всех изменений состояний заказов

---

## 7. token_usage (Использование Токенов)

```sql
CREATE TABLE token_usage (
    id BIGSERIAL PRIMARY KEY,

    -- Ссылки
    order_id BIGINT REFERENCES orders(id) ON DELETE CASCADE,
    message_id BIGINT REFERENCES messages(id) ON DELETE SET NULL,

    -- Агент
    agent_name VARCHAR(50),
    model VARCHAR(100),

    -- Токены
    input_tokens INT DEFAULT 0,
    output_tokens INT DEFAULT 0,
    total_tokens INT DEFAULT 0,

    -- Стоимость
    cost_usd DECIMAL(10, 6) DEFAULT 0,

    -- Временная метка
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_token_usage_order_id ON token_usage(order_id);
CREATE INDEX idx_token_usage_agent_name ON token_usage(agent_name);
CREATE INDEX idx_token_usage_created_at ON token_usage(created_at);
```

**Описание**: Детальная статистика использования токенов

---

## Связи между таблицами

```
users (1) ──→ (N) orders
users (1) ──→ (N) messages
users (1) ──→ (N) payments

orders (1) ──→ (N) messages
orders (1) ──→ (N) payments
orders (1) ──→ (N) generation_jobs
orders (1) ──→ (N) state_transitions
orders (1) ──→ (N) token_usage

messages (1) ──→ (1) token_usage
```

---

## Row Level Security (RLS)

Для безопасности данных в Supabase:

```sql
-- Включить RLS для всех таблиц
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE payments ENABLE ROW LEVEL SECURITY;
ALTER TABLE generation_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE state_transitions ENABLE ROW LEVEL SECURITY;
ALTER TABLE token_usage ENABLE ROW LEVEL SECURITY;

-- Политика: сервис имеет полный доступ
CREATE POLICY service_full_access ON users FOR ALL USING (true);
CREATE POLICY service_full_access ON orders FOR ALL USING (true);
CREATE POLICY service_full_access ON messages FOR ALL USING (true);
CREATE POLICY service_full_access ON payments FOR ALL USING (true);
CREATE POLICY service_full_access ON generation_jobs FOR ALL USING (true);
CREATE POLICY service_full_access ON state_transitions FOR ALL USING (true);
CREATE POLICY service_full_access ON token_usage FOR ALL USING (true);
```

---

## Индексы для производительности

Все важные индексы уже созданы в схемах выше:
- Foreign keys
- Поля для поиска (telegram_id, state, status)
- Временные метки для сортировки

---

## Триггеры

### Автоматическое обновление updated_at

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_users_updated_at BEFORE UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
```

---

## Примеры запросов

### Получить активные заказы пользователя
```sql
SELECT * FROM orders
WHERE user_id = $1
AND state NOT IN ('completed', 'cancelled')
ORDER BY created_at DESC;
```

### Статистика токенов по заказу
```sql
SELECT
    agent_name,
    SUM(total_tokens) as total_tokens,
    SUM(cost_usd) as total_cost
FROM token_usage
WHERE order_id = $1
GROUP BY agent_name;
```

### История состояний заказа
```sql
SELECT
    from_state,
    to_state,
    triggered_by,
    created_at
FROM state_transitions
WHERE order_id = $1
ORDER BY created_at ASC;
```
