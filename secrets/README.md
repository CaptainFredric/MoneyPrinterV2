# Secrets Setup

Use this folder for local-only secret notes/templates.
Do not store real secrets in tracked files.

## Gemini API Key

1. Copy `.env.example` to `.env` in repo root.
2. Set `GEMINI_API_KEY` in `.env`.
3. Export before running app:

```bash
set -a
source .env
set +a
```

`config.json` already supports env fallback: if `nanobanana2_api_key` is empty, the app uses `GEMINI_API_KEY`.
