"""Thread-safe, prompt-keyed cache for LLM recommendations.

The key is a hash of the exact prompt plus the model and prompt version. The
prompt already contains every context field (zone, soil type, crop, variety,
season, texture/SOC, product selection), so two analyses with the same N/P/K/pH
but different Ethiopian context can never share an entry.

Failures (None) are never cached: a transient API error must not become a
permanent false hit. The cache is in-process and bounded; it is a performance
optimization, not a training-data source.
"""

import copy
import hashlib
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future

import config

# Bump whenever build_prompt() changes meaningfully so old entries cannot be
# reused with a new prompt contract.
PROMPT_VERSION = "2026-10-22.2"


class LLMRecommendationCache:
    def __init__(self, maxsize=128, ttl_seconds=86400.0):
        self.maxsize = max(1, int(maxsize))
        self.ttl_seconds = max(0.0, float(ttl_seconds))
        self._cache = OrderedDict()
        self._inflight = {}
        self._lock = threading.RLock()
        self._enabled = True

    def make_key(self, prompt, lang, model):
        raw = f"{PROMPT_VERSION}\n{model}\n{lang}\n{prompt}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(self, key):
        if not self._enabled:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            created, value = entry
            if self.ttl_seconds and now - created > self.ttl_seconds:
                del self._cache[key]
                return None
            self._cache.move_to_end(key)
            return copy.deepcopy(value)

    def put(self, key, value):
        if not self._enabled or value is None:
            return
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (time.monotonic(), copy.deepcopy(value))
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def get_or_compute(self, key, compute):
        """Return a cached value or compute it once for concurrent callers."""
        if not self._enabled:
            return compute()
        cached = self.get(key)
        if cached is not None:
            return cached

        with self._lock:
            future = self._inflight.get(key)
            owner = future is None
            if owner:
                future = Future()
                self._inflight[key] = future

        if not owner:
            return future.result()

        try:
            value = compute()
            if value is not None:
                self.put(key, value)
            future.set_result(value)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            # Mark the exception as observed even when no waiter arrived, so
            # a transient API failure does not emit an asyncio "never
            # retrieved" warning at GC time.
            future.exception()
            raise
        finally:
            with self._lock:
                self._inflight.pop(key, None)

    def clear(self):
        with self._lock:
            self._cache.clear()

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, value):
        self._enabled = bool(value)


recommendation_cache = LLMRecommendationCache(
    maxsize=config.LLM_CACHE_MAXSIZE,
    ttl_seconds=config.LLM_CACHE_TTL_SECONDS,
)
recommendation_cache.enabled = config.LLM_CACHE_ENABLED
