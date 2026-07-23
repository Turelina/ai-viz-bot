"""Supabase database access for the orders and messages tables."""
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
        """Reset the client so it is recreated on the next access."""
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

    # ── Delivery: persistent state ────────────────────────────────────────────

    def set_delivery_admin(self, order_id: int, admin_id: int) -> None:
        """Save the administrator who started delivery for this order."""
        self.client.table("orders").update(
            {"delivery_admin_id": admin_id}
        ).eq("id", order_id).execute()

    def clear_delivery_admin(self, order_id: int) -> None:
        """Clear delivery_admin_id after delivery completes."""
        self.client.table("orders").update(
            {"delivery_admin_id": None}
        ).eq("id", order_id).execute()

    def get_pending_deliveries(self) -> dict[int, int]:
        """Return {admin_id: order_id} for orders in delivery status."""
        result = (
            self.client.table("orders")
            .select("id, delivery_admin_id")
            .eq("status", "prompt_ready")
            .not_.is_("delivery_admin_id", "null")
            .execute()
        )
        return {row["delivery_admin_id"]: row["id"] for row in (result.data or [])}

    # ── Messages: conversation history ────────────────────────────────────────

    def save_message(self, order_id: int, role: str, content: str) -> None:
        """Save a message to an order's conversation history."""
        self.client.table("messages").insert({
            "order_id": order_id,
            "role": role,
            "content": content,
        }).execute()

    def get_messages(self, order_id: int) -> list[dict]:
        """Return an order's messages in chronological order."""
        result = (
            self.client.table("messages")
            .select("role, content, created_at")
            .eq("order_id", order_id)
            .order("created_at")
            .execute()
        )
        return result.data or []

    # ── Reference photos ──────────────────────────────────────────────────────

    def upload_reference_photo(self, photo_bytes: bytes, username: str, order_id: int, index: int = 1) -> str:
        """Upload a reference photo to Supabase Storage.

        Path: {username}/{order_id}.jpg, or {order_id}_2.jpg for an additional
        photo. Return a signed URL valid for one year.
        """
        suffix = f"_{index}" if index > 1 else ""
        filename = f"{username}/{order_id}{suffix}.jpg"
        self.client.storage.from_("reference-photos").upload(
            path=filename,
            file=photo_bytes,
            file_options={"content-type": "image/jpeg"},
        )
        result = self.client.storage.from_("reference-photos").create_signed_url(
            filename, expires_in=31536000  # One year.
        )
        return result["signedURL"]

    def update_reference_photo(self, order_id: int, url: str | None) -> None:
        """Save or clear an order's reference-photo URL."""
        self.client.table("orders").update(
            {"reference_photo_url": url}
        ).eq("id", order_id).execute()

    def get_orders_with_old_reference_photos(self, days: int = 7) -> list[dict]:
        """Return orders whose reference photo is older than N days."""
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
        """Delete a file from Supabase Storage."""
        self.client.storage.from_("reference-photos").remove([filename])


db = Database()
