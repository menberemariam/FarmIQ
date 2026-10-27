import sys
import threading
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ML.cache import LLMRecommendationCache  # noqa: E402
from ML.engine import analyze_soil, build_prompt  # noqa: E402


class TestLlmCache(unittest.TestCase):
    def setUp(self):
        self.cache = LLMRecommendationCache(maxsize=2, ttl_seconds=60)

    def test_key_includes_zone_and_soil_type(self):
        nitisol = build_prompt(analyze_soil(
            25, 10, 40, 5.5, zone="highland_nitisols", soil_type="nitisol"))
        vertisol = build_prompt(analyze_soil(
            25, 10, 40, 5.5, zone="highland_vertisols",
            soil_type="vertisol"))
        self.assertNotEqual(
            self.cache.make_key(nitisol, "en", "m"),
            self.cache.make_key(vertisol, "en", "m"))

    def test_none_is_never_cached(self):
        calls = []

        def compute():
            calls.append(1)
            return None

        key = self.cache.make_key("p", "en", "m")
        self.assertIsNone(self.cache.get_or_compute(key, compute))
        self.assertIsNone(self.cache.get_or_compute(key, compute))
        self.assertEqual(len(calls), 2)
        self.assertIsNone(self.cache.get(key))

    def test_hit_returns_a_copy(self):
        key = self.cache.make_key("p", "en", "m")
        value = {"Nitrogen": {"status": "adequate", "solution": "x"}}
        self.cache.put(key, value)
        first = self.cache.get(key)
        first["Nitrogen"]["solution"] = "mutated"
        second = self.cache.get(key)
        self.assertEqual(second["Nitrogen"]["solution"], "x")

    def test_disabled_bypasses_cache(self):
        calls = []
        self.cache.enabled = False

        def compute():
            calls.append(1)
            return {"ok": True}

        key = self.cache.make_key("p", "en", "m")
        self.cache.get_or_compute(key, compute)
        self.cache.get_or_compute(key, compute)
        self.assertEqual(len(calls), 2)

    def test_lru_eviction(self):
        self.cache.put(self.cache.make_key("a", "en", "m"), {"a": 1})
        self.cache.put(self.cache.make_key("b", "en", "m"), {"b": 1})
        self.cache.get(self.cache.make_key("a", "en", "m"))
        self.cache.put(self.cache.make_key("c", "en", "m"), {"c": 1})
        self.assertIsNone(self.cache.get(self.cache.make_key("b", "en", "m")))
        self.assertIsNotNone(self.cache.get(self.cache.make_key("a", "en", "m")))

    def test_concurrent_misses_compute_once(self):
        calls = []
        started = threading.Barrier(4)

        def compute():
            calls.append(1)
            threading.Event().wait(0.05)
            return {"ok": True}

        key = self.cache.make_key("p", "en", "m")
        results = []

        def worker():
            started.wait()
            results.append(self.cache.get_or_compute(key, compute))

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 4)
        self.assertTrue(all(result == {"ok": True} for result in results))


if __name__ == "__main__":
    unittest.main()
