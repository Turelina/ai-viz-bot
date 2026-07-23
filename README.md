# Telegram Bot for AI Image Orders
An MVP bot for automating AI visualization orders (exteriors, facades, landscapes). The client describes their request and pays — a multi-agent pipeline automatically verifies the payment, generates a prompt and the image, and then the admin delivers the result with a single click.

**Current version:** MVP 0.3.0

## How it works
Client: `/start` → description → payment screenshot
                                   ↓
              Vision Agent verifies payment (Claude Sonnet)
                                   ↓
         confidence > 0.9 → auto-confirmation    |   lower → manual check by admin
                                   ↓
              Engineer Agent generates prompt (Claude Sonnet)
                                   ↓
              Gemini Imagen 3 Pro generates image
                                   ↓
         Admin sees the ready image → [✅ Deliver to client] → Client receives the result
