"""
Утилита для автоматического создания отчетов об ошибках
"""
import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List
import os


class ErrorReporter:
    """Класс для создания структурированных отчетов об ошибках"""

    def __init__(self, logs_dir: str = "logs"):
        """
        Инициализация reporter

        Args:
            logs_dir: Корневая папка для логов
        """
        self.logs_dir = Path(logs_dir)
        self.error_counter = self._get_today_error_count()

    def _get_today_error_count(self) -> int:
        """Получить количество ошибок сегодня для генерации ID"""
        today = datetime.now().strftime("%Y%m%d")
        # Подсчитываем файлы с сегодняшней датой
        count = 0
        for file in self.logs_dir.rglob(f"*{today}*.json"):
            count += 1
        return count + 1

    def _generate_error_id(self) -> str:
        """Генерировать уникальный ID ошибки"""
        today = datetime.now().strftime("%Y%m%d")
        error_id = f"ERR-{today}-{self.error_counter:03d}"
        self.error_counter += 1
        return error_id

    def log_error(
        self,
        error: Exception,
        severity: str = "medium",
        category: str = "system",
        agent_name: Optional[str] = None,
        order_id: Optional[int] = None,
        user_id: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None,
        attempted_solution: Optional[Dict[str, str]] = None,
        recommendations: Optional[List[str]] = None,
    ) -> str:
        """
        Логировать ошибку и создать отчет

        Args:
            error: Объект исключения
            severity: Уровень серьезности (critical, high, medium, low, info)
            category: Категория (agent, system, development, database, api)
            agent_name: Имя агента (если применимо)
            order_id: ID заказа (если применимо)
            user_id: ID пользователя (если применимо)
            context: Контекст ошибки
            attempted_solution: Что было попытано сделать
            recommendations: Рекомендации по исправлению

        Returns:
            Путь к созданному файлу отчета
        """
        # Генерируем ID ошибки
        error_id = self._generate_error_id()

        # Создаем отчет
        report = {
            "timestamp": datetime.now().isoformat() + "Z",
            "error_id": error_id,
            "severity": severity,
            "category": category,
            "agent_name": agent_name,
            "order_id": order_id,
            "user_id": user_id,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
                "code": getattr(error, "code", None),
                "details": getattr(error, "details", {}),
            },
            "context": context or {},
            "stack_trace": traceback.format_exc(),
            "attempted_solution": attempted_solution or {
                "action": None,
                "result": None,
                "reason": None,
            },
            "resolution": {
                "status": "unresolved",
                "assigned_to": None,
                "notes": None,
                "solution": None,
            },
            "impact": {
                "user_affected": user_id is not None,
                "order_blocked": order_id is not None,
                "system_down": severity == "critical",
                "data_lost": False,
                "tokens_wasted": 0,
                "cost_usd": 0.0,
            },
            "recommendations": recommendations or [],
            "related_errors": [],
        }

        # Определяем путь для сохранения
        file_path = self._get_file_path(
            category=category,
            agent_name=agent_name,
            severity=severity,
            error_id=error_id,
            order_id=order_id,
        )

        # Создаем папку если не существует
        file_path.parent.mkdir(parents=True, exist_ok=True)

        # Сохраняем отчет
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        # Выводим в консоль
        self._print_error_summary(report, file_path)

        return str(file_path)

    def _get_file_path(
        self,
        category: str,
        agent_name: Optional[str],
        severity: str,
        error_id: str,
        order_id: Optional[int],
    ) -> Path:
        """Определить путь для сохранения отчета"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        # Для агентов - сохраняем в папку агента
        if category == "agent" and agent_name:
            folder = self.logs_dir / "agents" / agent_name
            if order_id:
                filename = f"{timestamp}_order-{order_id}_{error_id}.json"
            else:
                filename = f"{timestamp}_{error_id}.json"

        # Для критических ошибок - в errors/
        elif severity == "critical":
            folder = self.logs_dir / "errors"
            filename = f"{timestamp}_CRITICAL_{error_id}.json"

        # Для разработки - в development/
        elif category == "development":
            folder = self.logs_dir / "development"
            filename = f"{timestamp}_{error_id}.json"

        # Для системных - в system/
        else:
            folder = self.logs_dir / "system"
            filename = f"{timestamp}_{error_id}.json"

        return folder / filename

    def _print_error_summary(self, report: Dict[str, Any], file_path: Path):
        """Вывести краткую информацию об ошибке в консоль"""
        severity_emoji = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🟢",
            "info": "ℹ️",
        }

        emoji = severity_emoji.get(report["severity"], "⚠️")

        print("\n" + "=" * 60)
        print(f"{emoji} ОШИБКА: {report['error_id']}")
        print("=" * 60)
        print(f"Серьезность: {report['severity'].upper()}")
        print(f"Категория: {report['category']}")
        if report["agent_name"]:
            print(f"Агент: {report['agent_name']}")
        print(f"Тип: {report['error']['type']}")
        print(f"Сообщение: {report['error']['message']}")
        print(f"Отчет сохранен: {file_path}")
        print("=" * 60 + "\n")

    def log_development_error(
        self, error: Exception, context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Быстрый метод для логирования ошибок разработки

        Args:
            error: Объект исключения
            context: Контекст

        Returns:
            Путь к файлу отчета
        """
        return self.log_error(
            error=error,
            severity="medium",
            category="development",
            context=context,
            recommendations=["Проверить код", "Добавить обработку ошибки"],
        )

    def log_agent_error(
        self,
        agent_name: str,
        error: Exception,
        order_id: Optional[int] = None,
        user_id: Optional[int] = None,
        severity: str = "medium",
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Логировать ошибку агента

        Args:
            agent_name: Имя агента
            error: Объект исключения
            order_id: ID заказа
            user_id: ID пользователя
            severity: Серьезность
            context: Контекст

        Returns:
            Путь к файлу отчета
        """
        return self.log_error(
            error=error,
            severity=severity,
            category="agent",
            agent_name=agent_name,
            order_id=order_id,
            user_id=user_id,
            context=context,
        )

    def log_critical_error(
        self,
        error: Exception,
        context: Optional[Dict[str, Any]] = None,
        impact: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Логировать критическую ошибку

        Args:
            error: Объект исключения
            context: Контекст
            impact: Влияние на систему

        Returns:
            Путь к файлу отчета
        """
        # TODO: Отправить уведомление администратору
        return self.log_error(
            error=error,
            severity="critical",
            category="system",
            context=context,
            recommendations=[
                "Немедленно проверить систему",
                "Уведомить администратора",
                "Проверить логи базы данных",
            ],
        )


# Глобальный экземпляр
error_reporter = ErrorReporter()


# Декоратор для автоматического логирования ошибок
def log_errors(
    severity: str = "medium",
    category: str = "system",
    agent_name: Optional[str] = None,
):
    """
    Декоратор для автоматического логирования ошибок в функциях

    Пример:
        @log_errors(severity="high", agent_name="manager")
        def process_order(order_id):
            # код
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                error_reporter.log_error(
                    error=e,
                    severity=severity,
                    category=category,
                    agent_name=agent_name,
                    context={
                        "function": func.__name__,
                        "args": str(args),
                        "kwargs": str(kwargs),
                    },
                )
                raise  # Пробрасываем ошибку дальше

        return wrapper

    return decorator
