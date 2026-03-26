#!/usr/bin/env python3
"""Simple API prototype (Flask) for a headline-quality analyzer.

Run quick smoke test:
  .runtime-venv/bin/python scripts/api_prototype.py --test

Run as a server:
  .runtime-venv/bin/python scripts/api_prototype.py
  then query: GET http://127.0.0.1:8081/analyze_headline?text=Your%20headline

This is a minimal, self-contained example to iterate into a deployable API MVP.
"""
from __future__ import annotations

import argparse
import math
from flask import Flask, request, jsonify

app = Flask(__name__)


def analyze_headline(text: str) -> dict:
    """Return a simple heuristic analysis of a headline.

    This is intentionally lightweight: the goal is a useful, defensible
    API that can be expanded later with ML models.
    """
    if not isinstance(text, str):
        text = str(text or "")
    txt = text.strip()
    length = len(txt)
    words = txt.split()
    word_count = len(words)
    exclamations = txt.count("!")
    caps_words = sum(1 for w in words if any(c.isalpha() for c in w) and w.isupper())
    caps_ratio = (caps_words / word_count) if word_count else 0.0

    # Very small heuristic scoring (0..100)
    score = 50
    # reward moderate length (30-80 chars)
    if 30 <= length <= 80:
        score += 20
    else:
        score -= max(0, (abs(55 - length) // 5))

    # reward presence of numbers (listicles / promise of quantifiable value)
    if any(char.isdigit() for char in txt):
        score += 10

    # penalize too many exclamations
    score -= min(20, exclamations * 8)

    # small bonus for some caps (for emphasis) but penalize all-caps
    if 0 < caps_ratio < 0.4:
        score += 5
    if caps_ratio >= 0.6:
        score -= 15

    # normalize
    score = max(0, min(100, int(score)))

    suggestions = []
    if length < 30:
        suggestions.append("Consider making the headline a bit longer and more specific.")
    if length > 120:
        suggestions.append("Headline is long; try to shorten to the core message.")
    if exclamations > 1:
        suggestions.append("Avoid multiple exclamation marks; they can look spammy.")
    if caps_ratio >= 0.6:
        suggestions.append("Avoid ALL-CAPS words; they reduce credibility.")
    if any(char.isdigit() for char in txt) is False:
        suggestions.append("If appropriate, include a number to increase specificity (eg '5 ways').")

    return {
        "text": txt,
        "score": score,
        "length": length,
        "word_count": word_count,
        "exclamation_count": exclamations,
        "caps_ratio": round(caps_ratio, 2),
        "suggestions": suggestions,
    }


@app.route("/analyze_headline", methods=["GET", "POST"])
def endpoint_analyze_headline():
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        text = payload.get("text") or payload.get("headline")
    else:
        text = request.args.get("text") or request.args.get("headline")

    if not text:
        return jsonify({"error": "missing 'text' parameter"}), 400

    result = analyze_headline(text)
    return jsonify(result)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


def _run_test():
    """Run a quick internal smoke test using Flask test client."""
    example = "How I made $6,000 a month from a tiny weekend project"
    with app.test_client() as c:
        rv = c.get("/analyze_headline", query_string={"text": example})
        print("Status:", rv.status_code)
        print("Response:", rv.get_json())


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run internal smoke test and exit")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()

    if args.test:
        _run_test()
    else:
        app.run(host=args.host, port=args.port)
