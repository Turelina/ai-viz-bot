# AI Viz Bot Development Guide

## Purpose

This repository contains a Telegram bot that automates AI architectural-visualization orders. The production flow collects a client request and reference photo, verifies a payment screenshot, creates an English image prompt, optionally generates images, and lets an administrator deliver the result.

## Project Structure

- `main.py` starts the bot.
- `src/integrations/telegram/bot.py` implements Telegram handlers and the multi-agent pipeline.
- `src/core/database.py` provides Supabase access.
- `config/settings.py` loads environment configuration.
- `config/prompts.py` defines the agent prompts.
- `scripts/setup_database.py` prints SQL for the database schema.

## Development Rules

- Keep client-facing text, comments, docstrings, and documentation in English.
- Preserve the payment-verification, prompt-generation, and delivery pipeline when changing the bot.
- Never commit `.env` files or real credentials.
- Run syntax checks and relevant tests before committing changes.
