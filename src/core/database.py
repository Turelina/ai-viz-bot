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


db = Database()
