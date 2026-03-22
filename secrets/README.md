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
Twitter header, 1500x500 pixels, deep forest green to dark emerald horizontal gradient (#0f2318 to #1e5c3a), subtle faint notebook grid lines overlay at 5% opacity, three small flat icons spaced evenly (a checkmark circle, a timer, a lightning bolt), icons in soft off-white, left third empty for future text, clean professional productivity aesthetic, no text, no people, matte finishTwitter header, 1500x500 pixels, deep forest green to dark emerald horizontal gradient (#0f2318 to #1e5c3a), subtle faint notebook grid lines overlay at 5% opacity, three small flat icons spaced evenly (a checkmark circle, a timer, a lightning bolt), icons in soft off-white, left third empty for future text, clean professional productivity aesthetic, no text, no people, matte finish