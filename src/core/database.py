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

    def reset(self) -> None:
        """Сбрасывает клиент — при следующем обращении будет пересоздан."""
        self._client = None

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

    # ── Фото-референсы ────────────────────────────────────────────────────────

    def upload_reference_photo(self, photo_bytes: bytes, username: str, order_id: int, index: int = 1) -> str:
        """Загружает фото-референс в Supabase Storage.
        Путь: {username}/{order_id}.jpg (или {order_id}_2.jpg для доп. фото).
        Возвращает signed URL на 1 год."""
        suffix = f"_{index}" if index > 1 else ""
        filename = f"{username}/{order_id}{suffix}.jpg"
        self.client.storage.from_("reference-photos").upload(
            path=filename,
            file=photo_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        result = self.client.storage.from_("reference-photos").create_signed_url(
            filename, expires_in=31536000  # 1 год
        )
        return result["signedURL"]

    def update_reference_photo(self, order_id: int, url: str | None) -> None:
        """Сохраняет или сбрасывает URL фото-референса к заказу."""
        self.client.table("orders").update(
            {"reference_photo_url": url}
        ).eq("id", order_id).execute()

    def get_orders_with_old_reference_photos(self, days: int = 7) -> list[dict]:
        """Возвращает заказы с референсом старше N дней."""
        from datetime import datetime, timedelta
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        result = (
            self.client.table("orders")
            .select("id, reference_photo_url")
            .not_.is_("reference_photo_url", "null")
            .lt("created_at", cutoff)
            .execute()
        )
        return result.data or []

    def delete_reference_photo_from_storage(self, filename: str) -> None:
        """Удаляет файл из Supabase Storage."""
        self.client.storage.from_("reference-photos").remove([filename])


db = Database()
