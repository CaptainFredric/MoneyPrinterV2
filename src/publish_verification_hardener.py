"""
Phase 3: Publish Verification Hardening

Purpose:
- Improve permalink resolution success rate from ~30% to 60%+
- Add enhanced matching strategies
- Better search-based fallback
- Cache warming for recent posts
- Social proof detection (retweets, likes)

Key improvements:
1. Expanded timeline collection (sample more posts)
2. Multi-strategy matching (url, text, search, social proof)
3. Batch search optimization
4. Timeline cache warmup before verification
5. Enhanced social proof detection
"""

import re
from typing import Optional


class PublishVerificationHardener:
    """Enhanced verification strategies for publish confirmation."""

    @staticmethod
    def extract_tweet_id(url: str) -> Optional[str]:
        """Extract tweet ID from various URL formats."""
        if not url:
            return None

        # Standard format: https://twitter.com/handle/status/123456
        match = re.search(r'/status/(\d+)', url)
        if match:
            return match.group(1)

        # X.com format: https://x.com/handle/status/123456
        match = re.search(r'x\.com/[^/]+/status/(\d+)', url)
        if match:
            return match.group(1)

        # Direct ID: just digits
        if re.match(r'^\d+$', url):
            return url

        return None

    @staticmethod
    def normalize_text_aggressive(text: str) -> str:
        """Aggressive text normalization for matching."""
        if not text:
            return ""

        # Remove URLs
        text = re.sub(r'https?://\S+', '', text)

        # Remove mentions/hashtags punctuation but keep words
        text = re.sub(r'[@#]\s*', '', text)

        # Normalize whitespace
        text = ' '.join(text.split())

        # Remove trailing punctuation
        text = text.rstrip('.,!?;:-')

        # Convert to lowercase
        text = text.lower()

        # Remove common filler words
        fillers = ['the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for']
        words = text.split()
        words = [w for w in words if w not in fillers or len(w) > 3]
        text = ' '.join(words)

        return text.strip()

    @staticmethod
    def compute_match_score(cached_text: str, live_text: str) -> float:
        """Compute match score between cached and live post text (0-100)."""
        if not cached_text or not live_text:
            return 0.0

        cached_norm = PublishVerificationHardener.normalize_text_aggressive(cached_text)
        live_norm = PublishVerificationHardener.normalize_text_aggressive(live_text)

        if not cached_norm or not live_norm:
            return 0.0

        # Exact match
        if cached_norm == live_norm:
            return 100.0

        # Substring match (cached is subset of live)
        if cached_norm in live_norm:
            overlap_ratio = len(cached_norm) / len(live_norm)
            return 85.0 + (15.0 * overlap_ratio)

        # Substring match (live is subset of cached)
        if live_norm in cached_norm:
            overlap_ratio = len(live_norm) / len(cached_norm)
            return 75.0 + (10.0 * overlap_ratio)

        # Character-level similarity (Jaro-Winkler style)
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, cached_norm, live_norm).ratio()
        return max(0.0, min(100.0, similarity * 100))

    @staticmethod
    def extract_hashtags(text: str) -> set[str]:
        """Extract hashtags from text."""
        hashtags = set()
        for match in re.finditer(r'#(\w+)', text or ''):
            hashtags.add(match.group(1).lower())
        return hashtags

    @staticmethod
    def extract_mentions(text: str) -> set[str]:
        """Extract mentions from text."""
        mentions = set()
        for match in re.finditer(r'@(\w+)', text or ''):
            mentions.add(match.group(1).lower())
        return mentions

    @staticmethod
    def compute_semantic_similarity(cached_text: str, live_text: str) -> float:
        """Compute semantic similarity using keyword extraction."""
        if not cached_text or not live_text:
            return 0.0

        # Extract keywords (words > 3 chars, not stopwords)
        def extract_keywords(text: str) -> set[str]:
            norm = PublishVerificationHardener.normalize_text_aggressive(text)
            stopwords = {
                'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
                'that', 'this', 'is', 'are', 'was', 'were', 'been', 'be', 'have', 'has',
            }
            words = norm.split()
            return {w for w in words if len(w) > 3 and w not in stopwords}

        cached_keywords = extract_keywords(cached_text)
        live_keywords = extract_keywords(live_text)

        if not cached_keywords or not live_keywords:
            return 0.0

        # Jaccard similarity
        intersection = len(cached_keywords & live_keywords)
        union = len(cached_keywords | live_keywords)

        if union == 0:
            return 0.0

        return (intersection / union) * 100

    @staticmethod
    def is_strong_match(cached_text: str, live_text: str, url_match: bool = False) -> bool:
        """Determine if cached and live posts are a strong match."""
        if not cached_text or not live_text:
            return url_match  # Fall back to URL match only

        # If URL already matched, very likely the same post
        if url_match:
            return True

        # Text-based matching thresholds
        match_score = PublishVerificationHardener.compute_match_score(cached_text, live_text)
        if match_score >= 80.0:
            return True

        # Semantic matching
        semantic_score = PublishVerificationHardener.compute_semantic_similarity(cached_text, live_text)
        if semantic_score >= 70.0:
            return True

        # Hashtag/mention consistency check
        cached_hashtags = PublishVerificationHardener.extract_hashtags(cached_text)
        live_hashtags = PublishVerificationHardener.extract_hashtags(live_text)

        if cached_hashtags and live_hashtags:
            hashtag_overlap = len(cached_hashtags & live_hashtags) / len(cached_hashtags | live_hashtags)
            if hashtag_overlap >= 0.5 and match_score >= 70.0:
                return True

        return False

    @staticmethod
    def build_search_queries(text: str, max_queries: int = 5) -> list[str]:
        """Build multiple search queries from post text for better coverage."""
        if not text:
            return []

        norm = PublishVerificationHardener.normalize_text_aggressive(text)
        if not norm:
            return []

        queries = []
        words = norm.split()
        significant_words = [w for w in words if len(w) >= 5]

        # Query 1: top distinctive words only
        if significant_words:
            queries.append(' '.join(significant_words[:3]))

        # Query 2: hashtags plus strongest keyword
        hashtags = sorted(PublishVerificationHardener.extract_hashtags(text))
        if hashtags:
            hashtag_query = ' '.join(f'#{tag}' for tag in hashtags[:2])
            if significant_words:
                hashtag_query = f"{hashtag_query} {significant_words[0]}".strip()
            queries.append(hashtag_query)

        # Query 3: alternate keyword cluster from later in the text
        if len(significant_words) >= 5:
            queries.append(' '.join(significant_words[2:5]))

        # Query 4: prefix keywords without quoting
        prefix_words = words[:6]
        prefix_sig = [w for w in prefix_words if len(w) >= 4]
        if prefix_sig:
            queries.append(' '.join(prefix_sig[:4]))

        # Remove duplicates, limit to max_queries
        queries = list(dict.fromkeys(queries))[:max_queries]

        return [q for q in queries if q and len(q) > 3]
