# MoneyPrinterV2 Linear Growth Path

This project now follows a **single active objective** model.

Instead of chasing many tweaks at once, we progress through one phase at a time.

## Phase 1 — Baseline Quality
**Goal:** reliable posts with strong hooks and no spam repeats.

**Exit criteria:**
- At least 14 posts per account
- Hook rate >= 70%
- Cooldown / skip behavior stable

## Phase 2 — Format Mix
**Goal:** balanced cadence across text, link, and media.

**Exit criteria:**
- Mode diversity >= 0.67
- Link ratio roughly 10–30%
- Media ratio roughly 8–20%

## Phase 3 — Credibility & Novelty
**Goal:** trustworthy and less repetitive content over longer time windows.

**Exit criteria:**
- Citation ratio roughly 10–30%
- Category diversity >= 0.25
- Angle diversity >= 0.60

## Phase 4 — Scale
**Goal:** increase volume while preserving quality.

**Exit criteria:**
- Stable quality metrics while posting more frequently
- No major regression in hooks/diversity/cadence

---

## Daily Operating Loop (Linear)
1. Run `python3 scripts/performance_report.py`
2. Read **Current Phase** and **Next Objective**
3. Keep changes aligned to that objective only
4. Re-check after 3–7 posts, then advance phase

This keeps the project directional and avoids optimization thrash.
