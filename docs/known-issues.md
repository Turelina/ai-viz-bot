# Known Issues

- A low-confidence payment-verification result requires administrator review.
- Automatic image generation is unavailable without a valid Gemini API key.
- The bot stores delivery state in the database, but in-memory generated-image bytes are unavailable after a restart; administrators can then deliver the result manually.
