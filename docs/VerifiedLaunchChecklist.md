# Verified Launch Checklist

This checklist is designed to get **MoneyPrinterV2** from clone → usable revenue workflows with explicit pass/fail checks.

It is based on these repo sources:
- `README.md`
- `scripts/setup_local.sh`
- `scripts/preflight_local.py`
- `src/main.py`
- `docs/Configuration.md`
- `docs/TwitterBot.md`
- `docs/AffiliateMarketing.md`
- `docs/YouTube.md`

## Verified Facts From This Workspace

As of **2026-03-21**, these facts were verified in the current workspace:

- `config.json` is **missing**.
- The configured workspace Python environment is **`.venv` with Python 3.9.6**.
- The project documentation states MPV2 needs **Python 3.12**.
- `scripts/setup_local.sh` creates and uses **`venv/`**, not `.venv/`.
- `python3.12` was **not found on PATH**.
- `firefox` was **not found on PATH**.
- `magick` and `convert` were **not found on PATH**.
- `scripts/setup_local.sh`, `scripts/preflight_local.py`, `src/main.py`, `src/classes/Twitter.py`, `src/classes/YouTube.py`, `src/classes/AFM.py`, and `src/classes/Outreach.py` show **no editor-detected errors**.

## Go / No-Go Summary

Do **not** expect the app to work yet in this workspace until these blockers are fixed:

- [ ] Install or expose **Python 3.12**.
- [ ] Create **`config.json`** from `config.example.json`.
- [ ] Install or expose **Firefox**.
- [ ] Install or expose **ImageMagick**.
- [ ] Ensure **Ollama** is running with at least one pulled model.
- [ ] Set **`nanobanana2_api_key`** or export **`GEMINI_API_KEY`**.
- [ ] Use a real, logged-in **Firefox profile directory** for X and YouTube automation.

---

## Phase 1 — Machine Setup

### 1. Confirm Python 3.12 is available

Run:

```bash
command -v python3.12
python3.12 --version
```

Pass when:
- `command -v python3.12` prints a path.
- Version prints `Python 3.12.x`.

Fail means:
- The repo is not using its documented Python baseline yet.

### 2. Confirm Firefox is installed

Run:

```bash
command -v firefox
```

Pass when:
- A Firefox binary path is printed.

If it is blank:
- Install Firefox, then sign into the X and YouTube accounts you want the bot to use.

### 3. Confirm ImageMagick is installed

Run:

```bash
command -v magick || command -v convert
```

Pass when:
- One executable path is printed.

Fail means:
- YouTube video generation may break when MoviePy/subtitle rendering needs ImageMagick.

### 4. Confirm Go is available if you want Outreach

Run:

```bash
command -v go
go version
```

Pass when:
- `go` is found and prints a version.

Skip if:
- You are not using menu option `4` (`Outreach`).

---

## Phase 2 — Repo Bootstrap

### 5. Create the repo-managed environment

The repo’s setup script expects `venv/`, not `.venv/`.

Run:

```bash
cd /Users/erendiracisneros/Documents/GitHub/PromisesFrontend/MoneyPrinterV2
bash scripts/setup_local.sh
```

Pass when:
- `venv/` exists.
- `config.json` is created.
- The script completes and prints the final start hint.

If it fails:
- Fix the missing system dependency shown in the script output, then rerun.

### 6. Activate the environment the repo expects

Run:

```bash
cd /Users/erendiracisneros/Documents/GitHub/PromisesFrontend/MoneyPrinterV2
source venv/bin/activate
python --version
```

Pass when:
- `python --version` prints `3.12.x`.

Fail means:
- You are still running the wrong interpreter and runtime issues are likely.

---

## Phase 3 — `config.json` Minimum Viable Configuration

### 7. Create `config.json`

If `scripts/setup_local.sh` did not create it, run:

```bash
cd /Users/erendiracisneros/Documents/GitHub/PromisesFrontend/MoneyPrinterV2
cp config.example.json config.json
```

Pass when:
- `config.json` exists at the repo root.

### 8. Fill the minimum fields that unblock generation

Set these in `config.json` before the first real run:

- [ ] `firefox_profile`
- [ ] `headless`
- [ ] `ollama_base_url`
- [ ] `ollama_model`
- [ ] `twitter_language`
- [ ] `nanobanana2_api_key` or environment variable `GEMINI_API_KEY`
- [ ] `imagemagick_path`
- [ ] `threads`
- [ ] `tts_voice`
- [ ] `font`
- [ ] `script_sentence_length`

Pass when:
- Every field above has a real value appropriate for your machine and accounts.

### 9. Fill Outreach-only fields if you plan to sell services

Set these only if using menu option `4`:

- [ ] `google_maps_scraper_niche`
- [ ] `scraper_timeout`
- [ ] `outreach_message_subject`
- [ ] `outreach_message_body_file`
- [ ] `email.smtp_server`
- [ ] `email.smtp_port`
- [ ] `email.username`
- [ ] `email.password`

Pass when:
- SMTP credentials are valid.
- The email body file exists and is ready to send.

---

## Phase 4 — Provider Readiness

### 10. Confirm Ollama is reachable and has a model

Run:

```bash
curl -sS http://127.0.0.1:11434/api/tags
```

Pass when:
- The response is JSON.
- The `models` list contains at least one model.

Recommended follow-up if empty:

```bash
ollama pull llama3.2:3b
```

### 11. Confirm Gemini image access is configured

Run one of these checks:

```bash
printf '%s\n' "$GEMINI_API_KEY"
```

or inspect `config.json` for:
- `nanobanana2_api_key`

Pass when:
- One of those is set to a real API key.

Fail means:
- `scripts/preflight_local.py` will report a blocking failure.

### 12. Confirm Firefox profile path is real

Run:

```bash
ls -d "$HOME/Library/Application Support/Firefox/Profiles"/*
```

Then set `firefox_profile` in `config.json` to the exact folder for the account you want to automate.

Pass when:
- The path exists.
- The profile is already logged in to X and/or YouTube.

---

## Phase 5 — Repo Preflight Gate

### 13. Run the repo preflight exactly as intended

Run:

```bash
cd /Users/erendiracisneros/Documents/GitHub/PromisesFrontend/MoneyPrinterV2
source venv/bin/activate
python scripts/preflight_local.py
```

Pass when:
- The script ends with `Preflight passed. Local setup looks ready.`

Current status in this workspace:
- This is **not expected to pass yet**, because `config.json` is missing and required external tools were not found on `PATH`.

If it fails:
- Fix only the first blocking issue it reports, rerun, and repeat until clean.

---

## Phase 6 — Money-Making Launch Order

This is the most pragmatic order for first revenue.

### 14. Launch the CLI

Run:

```bash
cd /Users/erendiracisneros/Documents/GitHub/PromisesFrontend/MoneyPrinterV2
source venv/bin/activate
python src/main.py
```

Pass when:
- The app starts.
- The banner prints.
- The main menu appears with these options from `src/constants.py`:
  - `YouTube Shorts Automation`
  - `Twitter Bot`
  - `Affiliate Marketing`
  - `Outreach`
  - `Quit`

### 15. Verify Twitter first

In the app:
1. Choose `2` (`Twitter Bot`).
2. Create one account entry.
3. Supply:
   - a nickname
   - the Firefox profile path
   - a topic
4. Choose `Post something`.

Pass when:
- The app reaches X compose.
- It enters text.
- It clicks the Post button.
- A new post entry appears in the Twitter cache when you choose `Show all Posts`.

This is the first channel to validate because:
- it is the fastest end-to-end loop,
- it creates audience infrastructure,
- it is reused by affiliate flow.

### 16. Verify Affiliate Marketing second

In the app:
1. Choose `3` (`Affiliate Marketing`).
2. Provide a real affiliate link.
3. Provide the UUID of the Twitter account created above.

Pass when:
- The product page loads.
- The app scrapes the title and features.
- It generates a pitch.
- It posts that pitch through the linked Twitter account.

Do this only after Twitter works.

### 17. Verify YouTube third

In the app:
1. Choose `1` (`YouTube Shorts Automation`).
2. Create a YouTube account entry.
3. Supply:
   - nickname
   - Firefox profile path
   - niche
   - language
4. Choose `Upload Short`.

Pass when:
- A video is generated.
- Metadata is generated.
- Upload prompt appears.
- Upload succeeds if you choose `Yes`.

This comes after Twitter because it has more moving parts:
- LLM script generation
- image generation
- TTS
- MoviePy assembly
- browser upload automation

### 18. Verify Outreach last

Use this only if you are selling a service to local businesses.

In the app:
1. Choose `4` (`Outreach`).
2. Make sure your niche and SMTP settings are already configured.

Pass when:
- The scraper downloads/builds.
- Results file is produced.
- The app extracts websites/emails.
- Emails send successfully.

This is last because it depends on:
- Go
- scraper download/build
- SMTP
- compliant outreach copy

---

## Recommended First Revenue Path

Use this exact order:

- [ ] Make `Twitter Bot` work with one narrow niche account.
- [ ] Post at least one generated tweet successfully.
- [ ] Connect one affiliate offer relevant to that niche.
- [ ] Post one affiliate pitch through the verified Twitter account.
- [ ] Only after that, add YouTube Shorts for the same niche.
- [ ] Only use Outreach if you are selling services, not just affiliate links.

Good first niches:
- software tools
- office gear
- productivity products
- creator tools
- local business marketing services

---

## Hard Stop Conditions

Stop and fix setup before trying to monetize if any of these are true:

- [ ] `python --version` is not `3.12.x`
- [ ] `config.json` does not exist
- [ ] `python scripts/preflight_local.py` does not pass
- [ ] `firefox_profile` is empty or invalid
- [ ] Ollama has no available model
- [ ] Gemini key is missing
- [ ] ImageMagick is not installed

If any box above is unchecked, the repo is **not launch-ready**.

---

## Definition of "Working"

Treat the setup as working only when all of the following are true:

- [ ] Preflight passes
- [ ] The main menu loads
- [ ] One tweet posts successfully
- [ ] One affiliate pitch posts successfully
- [ ] One short generates successfully
- [ ] Outreach runs successfully if you need it

Until then, the correct status is:
- **Partially configured**
- not **verified production-ready**
