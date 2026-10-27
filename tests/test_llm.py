import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from ML.cache import recommendation_cache  # noqa: E402
from ML.engine import analyze_soil  # noqa: E402
from ML.llm import (  # noqa: E402
    contract_counters,
    get_llm_recommendations,
    reset_contract_counters,
)

ANALYSIS = analyze_soil(50, 20, 120, 6.5)


class TestGetLlmRecommendations(unittest.TestCase):
    def setUp(self):
        recommendation_cache.clear()
        recommendation_cache.enabled = True
        reset_contract_counters()
        backend = mock.patch.dict(os.environ, {"LLM_BACKEND": "groq"},
                                  clear=False)
        backend.start()
        self.addCleanup(backend.stop)

    def tearDown(self):
        recommendation_cache.clear()

    def test_missing_key_returns_none(self):
        self.assertIsNone(get_llm_recommendations(ANALYSIS, ""))

    @mock.patch("langchain_ollama.ChatOllama")
    def test_ollama_backend_needs_no_api_key(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "ollama"},
                             clear=False):
            out = get_llm_recommendations(ANALYSIS, "", timeout=8.0)
        self.assertEqual(out, payload)

    @mock.patch("langchain_ollama.ChatOllama")
    def test_ollama_client_configuration_is_locked_down(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        env = {
            "LLM_BACKEND": "ollama",
            "OLLAMA_MODEL": "qwen2.5:3b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
            "LLM_MAX_TOKENS": "1536",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            get_llm_recommendations(ANALYSIS, "", timeout=8.0)
        kwargs = mock_chat.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen2.5:3b")
        self.assertEqual(kwargs["base_url"], "http://127.0.0.1:11434")
        self.assertEqual(kwargs["format"], "json")
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["client_kwargs"], {"timeout": 8.0})
        self.assertNotIn("timeout", kwargs)
        self.assertEqual(kwargs["num_predict"], 1536)

    def test_unknown_backend_falls_back_silently(self):
        with mock.patch.dict(os.environ, {"LLM_BACKEND": "bogus"},
                             clear=False):
            self.assertIsNone(get_llm_recommendations(ANALYSIS, "test-key"))

    @mock.patch("langchain_groq.ChatGroq")
    def test_success_returns_parsed_json(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        out = get_llm_recommendations(ANALYSIS, "test-key")
        self.assertEqual(out, payload)

    @mock.patch("langchain_groq.ChatGroq")
    def test_generation_parameters_are_locked_down(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        get_llm_recommendations(ANALYSIS, "test-key", timeout=8.0)
        kwargs = mock_chat.call_args.kwargs
        self.assertEqual(kwargs["temperature"], 0.0)
        self.assertEqual(kwargs["max_tokens"], 1024)
        self.assertEqual(kwargs["request_timeout"], 8.0)
        self.assertEqual(
            kwargs["model_kwargs"],
            {"response_format": {"type": "json_object"}},
        )
        self.assertNotIn("top_p", kwargs)

        messages = mock_chat.return_value.invoke.call_args.args[0]
        system = messages[0].content
        self.assertIn("JSON", system)
        for key in ("Nitrogen", "Phosphorus", "Potassium", "pH"):
            self.assertIn(key, system)
        self.assertIn("status", system)
        self.assertIn("solution", system)
        self.assertIn("no markdown", system)
        self.assertIn("truncated", system)
        self.assertIn("other than those four is rejected", system)
        self.assertIn("extra fields", system)

    @mock.patch("langchain_groq.ChatGroq")
    def test_truncated_response_returns_none_when_nothing_recoverable(self, mock_chat):
        mock_chat.return_value.invoke.return_value.content = (
            '{"Nitrogen": {"status": "adequ')
        mock_chat.return_value.invoke.return_value.response_metadata = {
            "finish_reason": "length"}
        self.assertIsNone(get_llm_recommendations(ANALYSIS, "test-key"))
        counters = contract_counters()
        self.assertEqual(counters["llm_truncations"], 1)
        self.assertEqual(counters["llm_parse_failures"], 1)

    @mock.patch("langchain_groq.ChatGroq")
    def test_truncated_response_salvages_complete_elements(self, mock_chat):
        mock_chat.return_value.invoke.return_value.content = (
            '{"Nitrogen": {"status": "adequate", "solution": "n"}, '
            '"Phosphorus": {"status": "adequate", "solution": "p"}, '
            '"Potassium": {"status": "adequate", "solution": "k"}, '
            '"pH": {"status": "adequ')
        mock_chat.return_value.invoke.return_value.response_metadata = {
            "finish_reason": "length"}
        out = get_llm_recommendations(ANALYSIS, "test-key")
        self.assertEqual(set(out), {"Nitrogen", "Phosphorus", "Potassium"})
        counters = contract_counters()
        self.assertEqual(counters["llm_truncations"], 1)
        self.assertEqual(counters["llm_elements_recovered"], 3)

    @mock.patch("langchain_groq.ChatGroq")
    def test_missing_response_metadata_is_safe(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        mock_chat.return_value.invoke.return_value.response_metadata = None
        self.assertEqual(
            get_llm_recommendations(ANALYSIS, "test-key"), payload)

    @mock.patch("langchain_groq.ChatGroq")
    def test_lowercase_payload_is_normalized(self, mock_chat):
        payload = {
            "nitrogen": {"status": "adequate", "solution": "x"},
            " phosphorus ": {"status": "adequate", "solution": "y"},
            "POTASSIUM": {"status": "adequate", "solution": "z"},
            "PH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        out = get_llm_recommendations(ANALYSIS, "test-key")
        self.assertEqual(set(out), {"Nitrogen", "Phosphorus", "Potassium", "pH"})
        self.assertEqual(out["pH"]["solution"], "w")

    @mock.patch("langchain_groq.ChatGroq")
    def test_unknown_keys_are_counted_as_contract_rejects(self, mock_chat):
        payload = {
            "nitrate": {"status": "adequate", "solution": "not an element"},
            "Nitrogen": {"status": "adequate", "solution": "x"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        out = get_llm_recommendations(ANALYSIS, "test-key")
        self.assertEqual(set(out), {"Nitrogen"})
        self.assertEqual(contract_counters()["llm_contract_rejects"], 1)

    @mock.patch("langchain_groq.ChatGroq")
    def test_max_tokens_env_override(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        with mock.patch.dict(os.environ, {"LLM_MAX_TOKENS": "1536"}):
            get_llm_recommendations(ANALYSIS, "test-key", timeout=8.0)
        self.assertEqual(mock_chat.call_args.kwargs["max_tokens"], 1536)

    @mock.patch("langchain_groq.ChatGroq")
    def test_zoned_analysis_prompt_carries_context(self, mock_chat):
        payload = {
            "Nitrogen": {"status": "adequate", "solution": "x"},
            "Phosphorus": {"status": "adequate", "solution": "y"},
            "Potassium": {"status": "adequate", "solution": "z"},
            "pH": {"status": "adequate", "solution": "w"},
        }
        mock_chat.return_value.invoke.return_value.content = json.dumps(payload)
        zoned = analyze_soil(
            50, 20, 120, 6.5, zone="highland_nitisols", soil_type="nitisol")
        get_llm_recommendations(zoned, "test-key")
        messages = mock_chat.return_value.invoke.call_args.args[0]
        human = messages[1].content
        self.assertIn("highland_nitisols", human)
        self.assertIn("nitisol", human)
        self.assertIn("do not substitute products", human)

    @mock.patch("langchain_groq.ChatGroq")
    def test_invoke_error_returns_none(self, mock_chat):
        mock_chat.return_value.invoke.side_effect = RuntimeError("network down")
        self.assertIsNone(get_llm_recommendations(ANALYSIS, "test-key"))

    @mock.patch("langchain_groq.ChatGroq")
    def test_timeout_returns_none(self, mock_chat):
        def slow(*args, **kwargs):
            time.sleep(0.3)
            raise AssertionError("should not reach")

        mock_chat.return_value.invoke.side_effect = slow
        start = time.monotonic()
        out = get_llm_recommendations(ANALYSIS, "test-key", timeout=0.01)
        elapsed = time.monotonic() - start
        self.assertIsNone(out)
        self.assertLess(elapsed, 2.0)

    @mock.patch("langchain_groq.ChatGroq")
    def test_invalid_json_returns_none(self, mock_chat):
        mock_chat.return_value.invoke.return_value.content = "not json at all"
        self.assertIsNone(get_llm_recommendations(ANALYSIS, "test-key"))
        counters = contract_counters()
        self.assertEqual(counters["llm_calls"], 1)
        self.assertEqual(counters["llm_parse_failures"], 1)


if __name__ == "__main__":
    unittest.main()
