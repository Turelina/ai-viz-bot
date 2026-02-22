"""
Работа с базой данных Supabase.
Две таблицы: orders и messages.
"""
from supabase import create_client, Client
from config.settings import settings


class Database:
    def __init__(self):
        self._client: Client | None = None

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = create_client(settings.supabase_url, settings.supabase_key)
        return self._client

    def create_order(self, user_id: int, username: str, description: str) -> dict:
        result = self.client.table("orders").insert({
            "user_id": user_id,
            "username": username,
            "description": description,
            "status": "awaiting_payment",
        }).execute()
        return result.data[0]

    def get_order(self, order_id: int) -> dict | None:
        result = self.client.table("orders").select("*").eq("id", order_id).execute()
        return result.data[0] if result.data else None

    def update_status(self, order_id: int, status: str) -> None:
        self.client.table("orders").update({"status": status}).eq("id", order_id).execute()

    def update_prompt(self, order_id: int, prompt: str) -> None:
        self.client.table("orders").update({"prompt": prompt}).eq("id", order_id).execute()

    def get_recent_orders(self, limit: int = 10) -> list[dict]:
        result = (
            self.client.table("orders")
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ── Доставка: персистентное состояние ────────────────────────────────────

    def set_delivery_admin(self, order_id: int, admin_id: int) -> None:
        """Сохраняет, какой админ начал доставку этого заказа."""
        self.client.table("orders").update(
            {"delivery_admin_id": admin_id}
        ).eq("id", order_id).execute()

    def clear_delivery_admin(self, order_id: int) -> None:
        """Сбрасывает delivery_admin_id после завершения доставки."""
        self.client.table("orders").update(
            {"delivery_admin_id": None}
        ).eq("id", order_id).execute()

    def get_pending_deliveries(self) -> dict[int, int]:
        """Возвращает {admin_id: order_id} для заказов в статусе доставки."""
        result = (
            self.client.table("orders")
            .select("id, delivery_admin_id")
            .eq("status", "prompt_ready")
            .not_.is_("delivery_admin_id", "null")
            .execute()
        )
        return {row["delivery_admin_id"]: row["id"] for row in (result.data or [])}

    # ── Сообщения: история переписки ─────────────────────────────────────────

    def save_message(self, order_id: int, role: str, content: str) -> None:
        """Сохраняет сообщение в историю переписки по заказу."""
        self.client.table("messages").insert({
            "order_id": order_id,
            "role": role,
            "content": content,
        }).execute()

    def get_messages(self, order_id: int) -> list[dict]:
        """Возвращает историю сообщений по заказу в хронологическом порядке."""
        result = (
            self.client.table("messages")
            .select("role, content, created_at")
            .eq("order_id", order_id)
            .order("created_at")
            .execute()
        )
        return result.data or []


db = Database()
