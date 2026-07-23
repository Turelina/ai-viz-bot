# Database Schema

The Supabase database has two primary tables:

- `orders` stores each order, client details, status, payment data, prompt, reference-photo URL, and delivery administrator.
- `messages` stores chronological conversation history for each order.

Run `python scripts/setup_database.py` to print the SQL required for a new installation or migration.
