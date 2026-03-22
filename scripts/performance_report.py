#!/usr/bin/env python3
"""
scripts/performance_report.py

Linear growth scoreboard for Twitter automation.

Usage:
  python scripts/performance_report.py
  python scripts/performance_report.py --json

Purpose:
  - Converts many quality signals into one current phase
  - Gives one next objective and concrete actions
  - Compares recent 7 posts vs previous 7 per account
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
TWITTER_CACHE = ROOT_DIR / ".mp" / "twitter.json"


def _load_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _extract_urls(text: str) -> list[str]:
    return re.findall(r"https?://[^\s)]+", text or "")


def _extract_hashtags(text: str) -> list[str]:
    return re.findall(r"#([A-Za-z0-9_]+)", text or "")


def _has_hook(text: str) -> bool:
    first_line = (text or "").splitlines()[0].strip() if text else ""
    if not first_line:
        return False
    first_sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip().lower()
    if "?" in first_sentence:
        return True
    starts = (
        "did you know",
        "what if",
        "why",
        "most people",
        "boost",
        "stop",
        "try",
        "here's how",
    )
    return first_sentence.startswith(starts)


def _infer_format(post: dict) -> str:
    fmt = str(post.get("format", "")).strip().lower()
    if fmt in ("text", "link", "media"):
        return fmt
    if _extract_urls(post.get("content", "")):
        return "link"
    return "text"


def _window_metrics(posts: list[dict]) -> dict:
    if not posts:
        return {
            "count": 0,
            "hook_rate": 0.0,
            "link_ratio": 0.0,
            "media_ratio": 0.0,
            "citation_ratio": 0.0,
            "mode_diversity": 0.0,
            "category_diversity": 0.0,
            "angle_diversity": 0.0,
            "avg_hashtags": 0.0,
            "avg_len": 0,
        }

    total = len(posts)
    hooks = sum(1 for p in posts if _has_hook(p.get("content", "")))
    links = sum(1 for p in posts if _extract_urls(p.get("content", "")))
    media = sum(1 for p in posts if _infer_format(p) == "media")
    citations = sum(1 for p in posts if str(p.get("citation_source", "")).strip() or "(source:" in p.get("content", "").lower())

    formats = [_infer_format(p) for p in posts]
    categories = [str(p.get("category", "")).strip().lower() for p in posts]
    categories = [c for c in categories if c and c != "general"]

    angles = [str(p.get("angle_signature", "")).strip().lower() for p in posts]
    angles = [a for a in angles if a]
    if not angles:
        angles = [" ".join((p.get("content", "") or "").split())[:60].lower() for p in posts]

    hashtags = [len(_extract_hashtags(p.get("content", ""))) for p in posts]
    lengths = [len(p.get("content", "")) for p in posts]

    return {
        "count": total,
        "hook_rate": round(hooks / total, 2),
        "link_ratio": round(links / total, 2),
        "media_ratio": round(media / total, 2),
        "citation_ratio": round(citations / total, 2),
        "mode_diversity": round(len(set(formats)) / 3, 2),
        "category_diversity": round((len(set(categories)) / total) if total else 0.0, 2),
        "angle_diversity": round((len(set(angles)) / total) if total else 0.0, 2),
        "avg_hashtags": round(sum(hashtags) / total, 2),
        "avg_len": int(sum(lengths) / total),
    }


def _split_windows(posts: list[dict]) -> tuple[list[dict], list[dict]]:
    recent = posts[-7:]
    prior = posts[-14:-7] if len(posts) > 7 else []
    return recent, prior


def _phase_from_metrics(metrics: dict, total_posts: int) -> tuple[str, str]:
    if total_posts < 14 or metrics["hook_rate"] < 0.70:
        return "Phase 1: Baseline Quality", "Build consistency and strong hooks"

    if metrics["mode_diversity"] < 0.67 or metrics["link_ratio"] < 0.10 or metrics["media_ratio"] < 0.08:
        return "Phase 2: Format Mix", "Balance text, link, and media cadence"

    if metrics["citation_ratio"] < 0.10 or metrics["angle_diversity"] < 0.60 or metrics["category_diversity"] < 0.25:
        return "Phase 3: Credibility & Novelty", "Improve sources and long-horizon originality"

    return "Phase 4: Scale", "Increase volume while preserving quality"


def _account_summary(account: dict) -> dict:
    posts = account.get("posts", [])
    recent, prior = _split_windows(posts)
    recent_metrics = _window_metrics(recent)
    prior_metrics = _window_metrics(prior)

    phase, objective = _phase_from_metrics(recent_metrics, len(posts))

    return {
        "nickname": account.get("nickname", "?"),
        "topic": account.get("topic", "?"),
        "total_posts": len(posts),
        "phase": phase,
        "objective": objective,
        "recent": recent_metrics,
        "prior": prior_metrics,
    }


def build_performance_report() -> dict:
    cache = _load_json(TWITTER_CACHE)
    accounts = cache.get("accounts", [])

    summaries = [_account_summary(acc) for acc in accounts]
    if summaries:
        phase_order = {
            "Phase 1: Baseline Quality": 1,
            "Phase 2: Format Mix": 2,
            "Phase 3: Credibility & Novelty": 3,
            "Phase 4: Scale": 4,
        }
        weakest = min(summaries, key=lambda s: phase_order.get(s["phase"], 99))
        global_phase = weakest["phase"]
        next_objective = weakest["objective"]
    else:
        global_phase = "Phase 1: Baseline Quality"
        next_objective = "Add accounts and generate initial post samples"

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "global_phase": global_phase,
        "next_objective": next_objective,
        "accounts": summaries,
    }


def print_performance_report(report: dict) -> None:
    w = 70
    print("=" * w)
    print(f"  MoneyPrinterV2 Linear Growth Report — {report['generated_at']}")
    print("=" * w)
    print(f"Current Phase : {report['global_phase']}")
    print(f"Next Objective: {report['next_objective']}")
    print("-" * w)

    if not report["accounts"]:
        print("No twitter accounts found in cache.")
        print("=" * w)
        return

    for acc in report["accounts"]:
        print(f"Account       : {acc['nickname']}")
        print(f"Topic         : {acc['topic']}")
        print(f"Total Posts   : {acc['total_posts']}")
        print(f"Phase         : {acc['phase']}")
        print(f"Objective     : {acc['objective']}")
        recent = acc["recent"]
        prior = acc["prior"]
        print(
            "Recent(7)     : "
            f"hooks={int(recent['hook_rate']*100)}% "
            f"link={int(recent['link_ratio']*100)}% "
            f"media={int(recent['media_ratio']*100)}% "
            f"cite={int(recent['citation_ratio']*100)}% "
            f"mode_div={recent['mode_diversity']} "
            f"angle_div={recent['angle_diversity']}"
        )
        if prior["count"]:
            print(
                "Prior(7)      : "
                f"hooks={int(prior['hook_rate']*100)}% "
                f"link={int(prior['link_ratio']*100)}% "
                f"media={int(prior['media_ratio']*100)}% "
                f"cite={int(prior['citation_ratio']*100)}% "
                f"mode_div={prior['mode_diversity']} "
                f"angle_div={prior['angle_diversity']}"
            )
        else:
            print("Prior(7)      : not enough historical data yet")
        print("-" * w)

    print("Execution Rule: Do not optimize all metrics at once.")
    print("Focus on the global Next Objective for the next 3-7 posts.")
    print("=" * w)


def main() -> None:
    parser = argparse.ArgumentParser(description="MoneyPrinterV2 linear growth report")
    parser.add_argument("--json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    report = build_performance_report()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_performance_report(report)


if __name__ == "__main__":
    main()
