"""
Примеры использования ErrorReporter
"""
import sys
from pathlib import Path

# Добавляем корень проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils.error_reporter import error_reporter, log_errors


# =====================================
# ПРИМЕР 1: Базовое логирование
# =====================================
def example_basic_error():
    """Простой пример логирования ошибки"""
    try:
        # Какой-то код который выдает ошибку
        result = 1 / 0
    except Exception as e:
        # Логируем ошибку
        error_reporter.log_development_error(
            error=e, context={"action": "testing division", "numbers": [1, 0]}
        )


# =====================================
# ПРИМЕР 2: Ошибка агента
# =====================================
def example_agent_error():
    """Пример логирования ошибки агента"""
    try:
        # Код агента-менеджера
        order_id = 42
        user_id = 123456789

        # Что-то пошло не так
        raise ValueError("Превышен лимит токенов для заказа")

    except Exception as e:
        # Логируем с полным контекстом
        error_reporter.log_agent_error(
            agent_name="manager",
            error=e,
            order_id=order_id,
            user_id=user_id,
            severity="high",
            context={
                "state": "collecting_requirements",
                "tokens_used": 55000,
                "tokens_limit": 50000,
                "conversation_length": 15,
            },
        )


# =====================================
# ПРИМЕР 3: Критическая ошибка
# =====================================
def example_critical_error():
    """Пример критической ошибки"""
    try:
        # База данных недоступна
        raise ConnectionError("Не удалось подключиться к Supabase")

    except Exception as e:
        error_reporter.log_critical_error(
            error=e,
            context={"service": "supabase", "retry_attempts": 3},
            impact={"system_down": True, "users_affected": "all"},
        )


# =====================================
# ПРИМЕР 4: Декоратор для автологирования
# =====================================
@log_errors(severity="high", agent_name="vision")
def check_payment_screenshot(screenshot_path: str, expected_amount: float):
    """
    Функция с автоматическим логированием ошибок

    Любая ошибка в этой функции автоматически залогируется
    """
    if not screenshot_path:
        raise ValueError("Скриншот не предоставлен")

    # ... код проверки оплаты


# =====================================
# ПРИМЕР 5: Ошибка с рекомендациями
# =====================================
def example_with_recommendations():
    """Ошибка с рекомендациями по исправлению"""
    try:
        # Какая-то проблема
        raise RuntimeError("Промпт слишком длинный")

    except Exception as e:
        error_reporter.log_error(
            error=e,
            severity="medium",
            category="agent",
            agent_name="engineer",
            context={"prompt_length": 500, "max_length": 300},
            attempted_solution={
                "action": "Попытка сжать промпт",
                "result": "failed",
                "reason": "Промпт уже максимально сжат",
            },
            recommendations=[
                "Разбить требования на части",
                "Использовать более короткие описания",
                "Добавить автоматическое резюмирование",
            ],
        )


# =====================================
# ЗАПУСК ПРИМЕРОВ
# =====================================
if __name__ == "__main__":
    print("🧪 Примеры использования ErrorReporter\n")

    print("1️⃣ Базовая ошибка разработки:")
    example_basic_error()

    print("\n2️⃣ Ошибка агента:")
    example_agent_error()

    print("\n3️⃣ Критическая ошибка:")
    example_critical_error()

    print("\n4️⃣ Функция с декоратором:")
    try:
        check_payment_screenshot(None, 1500.0)
    except ValueError:
        pass  # Ошибка уже залогирована декоратором

    print("\n5️⃣ Ошибка с рекомендациями:")
    example_with_recommendations()

    print("\n✅ Все примеры выполнены!")
    print(f"📁 Проверьте папку logs/ для просмотра отчетов")
