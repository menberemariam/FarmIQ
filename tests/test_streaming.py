import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config

# Keep the streaming tests off the demo database, like tests/test_api.py.
_tmpdir = tempfile.TemporaryDirectory()
config.DB_PATH = Path(_tmpdir.name) / "test_streaming.db"

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api.app)

PAYLOAD = {
    "N": 120,
    "P": 30,
    "K": 200,
    "ph": 6.2,
    "crop": "maize",
    "lang": "en",
    "use_llm": False,
}


def parse_sse(text):
    """Parse SSE blocks into (event name, decoded JSON payload) pairs."""
    events = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append((event, json.loads("\n".join(data))))
    return events


def post_stream(payload=None):
    response = client.post("/api/predict/stream", json=payload or PAYLOAD)
    return response, parse_sse(response.text)


def deterministic_context(payload=None):
    req = api.PredictRequest(**(payload or PAYLOAD))
    return api._prepare_prediction(req)


def valid_record(ctx, element="Nitrogen"):
    item = next(i for i in ctx["analysis"] if i["element"] == element)
    det = next(r for r in ctx["deterministic"] if r["element"] == element)
    lo, hi = item["band"]
    solution = (
        f"The ideal band is {lo:g}-{hi:g} kg/ha; keep the measured value "
        f"inside it and re-test next season."
    )
    return {"status": det["status"], "solution": solution}


def fake_stream(events):
    async def generator(*args, **kwargs):
        for event in events:
            yield event

    return generator


class TestStreamEndpoint(unittest.TestCase):
    def test_content_type_and_event_order(self):
        response, events = post_stream()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual([name for name, _ in events], ["snapshot", "done"])

    def test_use_llm_false_matches_the_non_streaming_contract(self):
        response, events = post_stream()
        self.assertEqual(response.status_code, 200)
        snapshot = events[0][1]
        done = events[1][1]
        full = client.post("/api/predict", json=PAYLOAD).json()

        self.assertEqual(snapshot["recommendations"], full["recommendations"])
        self.assertEqual(done["recommendations"], full["recommendations"])
        self.assertEqual(snapshot["llm_used"], False)
        self.assertEqual(done["validated"], [])
        self.assertEqual(done["rejected"], [])
        self.assertFalse(done["truncated"])
        self.assertIsNone(done["reason"])
        self.assertTrue(
            all(r["source"] == "rule-based" for r in snapshot["recommendations"]))

    def test_validated_element_event_is_emitted_once(self):
        ctx = deterministic_context()
        record = valid_record(ctx)
        payload = {**PAYLOAD, "use_llm": True}
        stream = fake_stream([
            {"type": "element", "element": "Nitrogen", "record": record},
            {"type": "done", "elements": 1, "reason": None,
             "truncated": False, "cached": False},
        ])
        with mock.patch("api.stream_llm_recommendations", stream):
            response, events = post_stream(payload)

        self.assertEqual([name for name, _ in events],
                         ["snapshot", "element", "done"])
        element = events[1][1]
        self.assertEqual(element["element"], "Nitrogen")
        self.assertEqual(element["source"], "llm")
        done = events[2][1]
        self.assertEqual(done["validated"], ["Nitrogen"])
        self.assertEqual(done["rejected"], [])
        self.assertTrue(done["llm_used"])

    def test_status_mismatch_is_rejected_without_an_element_event(self):
        ctx = deterministic_context()
        record = valid_record(ctx)
        record["status"] = (
            "deficient" if record["status"] != "deficient" else "adequate")
        payload = {**PAYLOAD, "use_llm": True}
        stream = fake_stream([
            {"type": "element", "element": "Nitrogen", "record": record},
            {"type": "done", "elements": 1, "reason": None,
             "truncated": False, "cached": False},
        ])
        with mock.patch("api.stream_llm_recommendations", stream):
            _, events = post_stream(payload)

        self.assertEqual([name for name, _ in events], ["snapshot", "done"])
        done = events[1][1]
        self.assertEqual(done["validated"], [])
        self.assertIn("Nitrogen", done["rejected"])
        self.assertFalse(done["llm_used"])

    def test_numeric_mismatch_is_rejected(self):
        ctx = deterministic_context()
        det = next(r for r in ctx["deterministic"]
                   if r["element"] == "Nitrogen")
        payload = {**PAYLOAD, "use_llm": True}
        stream = fake_stream([
            {"type": "element", "element": "Nitrogen",
             "record": {"status": det["status"],
                        "solution": "Add fertilizer and re-test later."}},
            {"type": "done", "elements": 1, "reason": None,
             "truncated": False, "cached": False},
        ])
        with mock.patch("api.stream_llm_recommendations", stream):
            _, events = post_stream(payload)

        self.assertEqual([name for name, _ in events], ["snapshot", "done"])
        self.assertIn("Nitrogen", events[1][1]["rejected"])

    def test_missing_groq_key_falls_back_to_deterministic(self):
        payload = {**PAYLOAD, "use_llm": True}
        with mock.patch.dict(
            os.environ,
            {"LLM_BACKEND": "groq", "GROQ_API_KEY": ""},
            clear=False,
        ):
            with mock.patch("ML.llm.get_llm_client",
                            side_effect=AssertionError("no model call")):
                response, events = post_stream(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([name for name, _ in events], ["snapshot", "done"])
        done = events[1][1]
        self.assertFalse(done["llm_used"])
        self.assertEqual(done["reason"], "missing_key")
        self.assertTrue(
            all(r["source"] == "rule-based" for r in done["recommendations"]))

    def test_stream_exception_emits_error_then_done(self):
        async def boom(*args, **kwargs):
            yield {"type": "element", "element": "Nitrogen",
                   "record": {"status": "adequate", "solution": "x"}}
            raise RuntimeError("upstream failed")

        payload = {**PAYLOAD, "use_llm": True}
        with mock.patch("api.stream_llm_recommendations", boom):
            response, events = post_stream(payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([name for name, _ in events],
                         ["snapshot", "error", "done"])
        self.assertEqual(events[2][1]["reason"], "stream_error")
        self.assertTrue(
            all(r["source"] == "rule-based"
                for r in events[2][1]["recommendations"]))

    def test_invalid_input_is_a_json_validation_error(self):
        bad = {**PAYLOAD, "ph": 20}
        response = client.post("/api/predict/stream", json=bad)
        self.assertEqual(response.status_code, 422)
        self.assertFalse(
            response.headers["content-type"].startswith("text/event-stream"))
        self.assertIn("detail", response.json())


if __name__ == "__main__":
    unittest.main()
