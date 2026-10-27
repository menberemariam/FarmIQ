import asyncio
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ML.cache import recommendation_cache  # noqa: E402
from ML.engine import analyze_soil, build_prompt  # noqa: E402
from ML.llm import (  # noqa: E402
    DEFAULT_STREAM_IDLE_SECONDS,
    GROQ_MODEL,
    reset_contract_counters,
    stream_llm_recommendations,
)

ANALYSIS = analyze_soil(50, 20, 120, 6.5)

NITROGEN = {"status": "adequate", "solution": "n"}
PHOSPHORUS = {"status": "deficient", "solution": "p"}


class _Chunk:
    def __init__(self, content, metadata=None):
        self.content = content
        self.response_metadata = metadata or {}


class _FakeLLM:
    def __init__(self, chunks=None, error=None):
        self.chunks = list(chunks or [])
        self.error = error
        self.messages = None

    async def astream(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        for content in self.chunks:
            yield _Chunk(content)


class _SlowAfterFirst:
    """Emit one complete element, then stall to exercise the idle guard."""

    async def astream(self, messages):
        yield _Chunk(
            json.dumps({"Nitrogen": NITROGEN})
        )
        await asyncio.sleep(5)


async def _collect(**kwargs):
    return [
        event
        async for event in stream_llm_recommendations(ANALYSIS, **kwargs)
    ]


class TestStreamLlmRecommendations(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        recommendation_cache.clear()
        recommendation_cache.enabled = True
        reset_contract_counters()
        env = mock.patch.dict(
            os.environ,
            {"LLM_BACKEND": "groq", "LLM_STREAM_IDLE_SECONDS": "5"},
            clear=False,
        )
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        recommendation_cache.clear()

    async def test_complete_elements_are_emitted_once(self):
        chunks = [
            '{"Nitrogen": {"status": "adequate", "solution": "n"}, ',
            '"Phosphorus": {"status": "deficient", "solution": "p"}}',
        ]
        with mock.patch("ML.llm.get_llm_client", return_value=_FakeLLM(chunks)):
            events = await _collect(api_key="test-key")

        elements = [e for e in events if e["type"] == "element"]
        self.assertEqual([e["element"] for e in elements],
                         ["Nitrogen", "Phosphorus"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["elements"], 2)
        self.assertIsNone(events[-1]["reason"])
        self.assertFalse(events[-1]["cached"])

    async def test_partial_chunk_emits_nothing(self):
        chunks = ['{"Nitrogen": {"status": "adequate", "sol']
        with mock.patch("ML.llm.get_llm_client", return_value=_FakeLLM(chunks)):
            events = await _collect(api_key="test-key")

        self.assertFalse([e for e in events if e["type"] == "element"])
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["reason"], "parse_failed")

    async def test_truncated_response_salvages_complete_elements(self):
        chunks = [
            '{"Nitrogen": {"status": "adequate", "solution": "n"}, '
            '"Phosphorus": {"status": "defi'
        ]
        with mock.patch("ML.llm.get_llm_client", return_value=_FakeLLM(chunks)):
            events = await _collect(api_key="test-key")

        elements = [e for e in events if e["type"] == "element"]
        self.assertEqual([e["element"] for e in elements], ["Nitrogen"])
        self.assertEqual(events[-1]["type"], "done")

    async def test_cache_hit_emits_records_without_calling_the_model(self):
        payload = json.dumps({"Nitrogen": NITROGEN})
        with mock.patch("ML.llm.get_llm_client",
                        return_value=_FakeLLM([payload])):
            first = await _collect(api_key="test-key")
        self.assertEqual(first[-1]["cached"], False)

        with mock.patch("ML.llm.get_llm_client",
                        side_effect=AssertionError("model must not be called")):
            second = await _collect(api_key="test-key")

        elements = [e for e in second if e["type"] == "element"]
        self.assertEqual([e["element"] for e in elements], ["Nitrogen"])
        self.assertTrue(second[-1]["cached"])
        self.assertEqual(second[-1]["elements"], 1)

    async def test_missing_key_is_a_clean_terminal_event(self):
        with mock.patch("ML.llm.get_llm_client",
                        side_effect=AssertionError("model must not be called")):
            events = await _collect(api_key="")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "done")
        self.assertEqual(events[0]["reason"], "missing_key")

    async def test_ollama_backend_does_not_need_a_key(self):
        fake = _FakeLLM([json.dumps({"Nitrogen": NITROGEN})])
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"},
                             clear=False):
            with mock.patch("ML.llm.get_llm_client",
                            return_value=fake) as client:
                events = await _collect(api_key="")

        self.assertEqual([e for e in events if e["type"] == "element"][0]
                         ["element"], "Nitrogen")
        self.assertEqual(client.call_args.kwargs["backend"], "ollama")

    async def test_idle_stall_keeps_recovered_elements(self):
        with mock.patch.dict(
            os.environ,
            {"LLM_STREAM_TIMEOUT_SECONDS": "10",
             "LLM_STREAM_IDLE_SECONDS": "0.05"},
            clear=False,
        ):
            with mock.patch("ML.llm.get_llm_client",
                            return_value=_SlowAfterFirst()):
                events = await _collect(api_key="test-key")

        self.assertEqual([e["element"] for e in events
                          if e["type"] == "element"], ["Nitrogen"])
        self.assertEqual(events[-1]["reason"], "idle_timeout")
        self.assertTrue(events[-1]["truncated"])

    async def test_total_timeout_is_reported(self):
        with mock.patch.dict(
            os.environ,
            {"LLM_STREAM_TIMEOUT_SECONDS": "0.05",
             "LLM_STREAM_IDLE_SECONDS": "5"},
            clear=False,
        ):
            with mock.patch("ML.llm.get_llm_client",
                            return_value=_SlowAfterFirst()):
                events = await _collect(api_key="test-key")

        self.assertEqual(events[-1]["reason"], "timeout")
        self.assertTrue(events[-1]["truncated"])

    async def test_timeout_results_are_not_cached(self):
        prompt = build_prompt(ANALYSIS, lang="en", selection=None)
        key = recommendation_cache.make_key(prompt, "en", GROQ_MODEL)
        with mock.patch.dict(
            os.environ,
            {"LLM_STREAM_TIMEOUT_SECONDS": "0.05",
             "LLM_STREAM_IDLE_SECONDS": "5"},
            clear=False,
        ):
            with mock.patch("ML.llm.get_llm_client",
                            return_value=_SlowAfterFirst()):
                await _collect(api_key="test-key")

        self.assertIsNone(recommendation_cache.get(key))

    async def test_transport_error_is_a_clean_terminal_event(self):
        with mock.patch("ML.llm.get_llm_client",
                        return_value=_FakeLLM(error=RuntimeError("down"))):
            events = await _collect(api_key="test-key")

        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["reason"], "stream_error")
        self.assertFalse(events[-1]["truncated"])

    def test_idle_default_is_eight_seconds(self):
        self.assertEqual(DEFAULT_STREAM_IDLE_SECONDS, 8.0)


if __name__ == "__main__":
    unittest.main()
