# Data Flow

1. The client starts an order, provides a description and reference photo, and sends a payment screenshot.
2. The bot creates an order in Supabase and Claude Vision evaluates the payment.
3. High-confidence payments proceed automatically; other payments are sent to an administrator.
4. The Engineer Agent creates an English prompt and Gemini optionally generates image options.
5. An administrator delivers the selected image to the client.
