"""Optional LLM enrichment for soil recommendations.

Shared by the Streamlit UI (main.py) and the REST API (api.py). The helper
never raises: a missing key, network error, timeout or malformed JSON all
return None so callers fall back to the deterministic engine.

Groq is the default backend. Set ``LLM_BACKEND=ollama`` to use a local Ollama
server instead (no API key needed); the prompt, validation, timeout and cache
semantics are identical, so the deterministic engine remains authoritative.

The call runs in a worker thread with a hard timeout (LLM_TIMEOUT_SECONDS,
default 8s) so a hung request can never block a prediction.

The response contract is enforced twice: the system prompt names the four
element keys, and ``parse_llm_json_partial`` normalizes casing/whitespace and
salvages complete element records when the response is truncated.
"""

import asyncio
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from ML.cache import recommendation_cache
from ML.engine import build_prompt, parse_llm_json_partial

DEFAULT_TIMEOUT_SECONDS = 8.0
DEFAULT_STREAM_TIMEOUT_SECONDS = 30.0
DEFAULT_STREAM_IDLE_SECONDS = 8.0
DEFAULT_MAX_TOKENS = 1024
GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_MODEL = "llama3.2:3b"
OLLAMA_BASE_URL = "http://localhost:11434"
LLM_TEMPERATURE = 0.0
LLM_MAX_TOKENS = DEFAULT_MAX_TOKENS
VALID_LLM_BACKENDS = ("groq", "ollama")

SYSTEM_PROMPT = (
    "You are a JSON API that returns Ethiopian soil-fertility advice. "
    "Respond with ONLY one valid JSON object: no markdown, no code fences, "
    "no text before or after it. The object must have exactly four top-level "
    'keys: "Nitrogen", "Phosphorus", "Potassium", "pH". Any top-level key '
    "other than those four is rejected; do not add notes or extra fields. "
    'Each value must be an object with exactly two string keys: "status" and '
    '"solution". Every '
    "solution must be a dense, highly detailed agronomic explanation of at "
    "least 100 words. A truncated response is discarded."
)

logger = logging.getLogger("soil.llm")

_CONTRACT_COUNTERS = (
    "llm_calls",
    "llm_parse_failures",
    "llm_truncations",
    "llm_elements_recovered",
    "llm_contract_rejects",
)
_counters = {name: 0 for name in _CONTRACT_COUNTERS}
_counters_lock = threading.Lock()


def _bump(name, amount=1):
    with _counters_lock:
        _counters[name] = _counters.get(name, 0) + amount


def contract_counters():
    """Snapshot of additive LLM contract counters (safe to expose on health)."""
    with _counters_lock:
        return dict(_counters)


def reset_contract_counters():
    with _counters_lock:
        for name in _CONTRACT_COUNTERS:
            _counters[name] = 0


def _timeout_seconds():
    raw = os.getenv("LLM_TIMEOUT_SECONDS")
    try:
        return float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _stream_timeout_seconds():
    raw = os.getenv("LLM_STREAM_TIMEOUT_SECONDS")
    try:
        return float(raw) if raw else DEFAULT_STREAM_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_STREAM_TIMEOUT_SECONDS


def _stream_idle_seconds():
    """Max gap between chunks before a stream is treated as stalled."""
    raw = os.getenv("LLM_STREAM_IDLE_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_STREAM_IDLE_SECONDS
    except ValueError:
        return DEFAULT_STREAM_IDLE_SECONDS
    return value if value > 0 else DEFAULT_STREAM_IDLE_SECONDS


def _max_tokens():
    raw = os.getenv("LLM_MAX_TOKENS")
    try:
        value = int(raw) if raw else DEFAULT_MAX_TOKENS
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return value if value > 0 else DEFAULT_MAX_TOKENS


def _log_call(outcome, elements=0, recovered=0, truncated=False):
    logger.info(
        "llm_call outcome=%s elements=%d recovered=%d truncated=%s",
        outcome, elements, recovered, truncated,
    )


def _backend():
    """Read ``LLM_BACKEND`` at call time so tests and deployments can switch."""
    raw = os.getenv("LLM_BACKEND", "groq").strip().lower()
    if raw not in VALID_LLM_BACKENDS:
        raise ValueError(f"Unsupported LLM_BACKEND: {raw!r}")
    return raw


def _ollama_model():
    return os.getenv("OLLAMA_MODEL", OLLAMA_MODEL).strip() or OLLAMA_MODEL


def _ollama_base_url():
    value = os.getenv("OLLAMA_BASE_URL", OLLAMA_BASE_URL).strip()
    return value or OLLAMA_BASE_URL


def _cache_model(backend):
    """Model component of the cache key; Groq keys stay byte-identical."""
    if backend == "ollama":
        return f"ollama:{_ollama_model()}"
    return GROQ_MODEL


def get_llm_client(api_key=None, timeout=None, backend=None):
    """Build the LangChain client for the selected backend.

    Groq is the default and keeps the exact constructor arguments used before
    this switch. Ollama uses native JSON mode and the same timeout/max-token
    limits; it does not need an API key. Any unsupported backend raises, which
    ``get_llm_recommendations`` converts to the usual silent fallback.
    """
    backend = backend or _backend()
    if timeout is None:
        timeout = _timeout_seconds()

    if backend == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=_ollama_model(),
            base_url=_ollama_base_url(),
            temperature=LLM_TEMPERATURE,
            format="json",
            # langchain-ollama 0.3.x has no ``timeout`` field; the HTTP client
            # accepts it through client_kwargs. The worker-thread timeout in
            # ``_call_llm`` remains the outer hard stop.
            client_kwargs={"timeout": timeout},
            num_predict=_max_tokens(),
        )

    if backend == "groq":
        if not api_key:
            raise ValueError("GROQ_API_KEY is required when LLM_BACKEND='groq'")
        from langchain_groq import ChatGroq

        return ChatGroq(
            groq_api_key=api_key,
            model_name=GROQ_MODEL,
            temperature=LLM_TEMPERATURE,
            max_tokens=_max_tokens(),
            request_timeout=timeout,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    raise ValueError(f"Unsupported LLM_BACKEND: {backend!r}")


def _call_llm(prompt, api_key, timeout, backend):
    """One LLM call. Exceptions propagate to the cache/compute wrapper."""
    from langchain.schema import HumanMessage, SystemMessage

    _bump("llm_calls")
    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=prompt),
    ]
    llm = get_llm_client(api_key=api_key, timeout=timeout, backend=backend)
    with ThreadPoolExecutor(max_workers=1) as executor:
        response = executor.submit(llm.invoke, messages).result(timeout=timeout)

    metadata = getattr(response, "response_metadata", None) or {}
    truncated = (
        metadata.get("finish_reason") == "length"
        or metadata.get("done_reason") == "length"
    )
    stats = {}
    recovered = parse_llm_json_partial(response.content, stats=stats)
    if stats.get("rejected"):
        _bump("llm_contract_rejects", stats["rejected"])
    if truncated:
        _bump("llm_truncations")
    if recovered:
        if truncated:
            _bump("llm_elements_recovered", len(recovered))
        _log_call("ok", elements=len(recovered),
                  recovered=len(recovered) if truncated else 0,
                  truncated=truncated)
        return recovered
    _bump("llm_parse_failures")
    _log_call("parse_failed", truncated=truncated)
    if truncated:
        raise ValueError("LLM response truncated at max_tokens")
    return None


def get_llm_recommendations(analysis, api_key, lang="en", timeout=None,
                            selection=None):
    """Return parsed LLM recommendations (dict keyed by element) or None.

    Single attempt, hard timeout, silent fallback — this must never raise.
    `selection` (optional product map from fertilizer_catalog) is forwarded
    to build_prompt so the LLM is told the nutrient contents of the products
    the report will actually name; None keeps the classic contents line.

    ``api_key`` is required only for Groq. With ``LLM_BACKEND=ollama`` the
    key may be empty and the local server is used instead.
    """
    try:
        backend = _backend()
        if backend == "groq" and not api_key:
            return None
        if timeout is None:
            timeout = _timeout_seconds()
        prompt = build_prompt(analysis, lang=lang, selection=selection)
        key = recommendation_cache.make_key(
            prompt, lang, _cache_model(backend))
        return recommendation_cache.get_or_compute(
            key, lambda: _call_llm(prompt, api_key, timeout, backend))
    except Exception:
        return None


def _chunk_text(chunk):
    """Extract text from an AIMessageChunk across LangChain versions."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "".join(parts)
    return ""


async def stream_llm_recommendations(analysis, api_key, lang="en", timeout=None,
                                     selection=None):
    """Yield validated-candidate records progressively from the LLM.

    This is the async counterpart to ``get_llm_recommendations``. It yields
    ``{"type": "element", "element": ..., "record": ...}`` for each complete
    element recovered from the growing response, then one terminal
    ``{"type": "done", ...}`` event. It never raises: missing keys, network
    errors, timeouts and malformed/truncated JSON all become a clean terminal
    event so the SSE endpoint can fall back to the deterministic engine.

    Intermediate records are *unvalidated*; callers must run them through
    ``merge_recommendations`` (or equivalent numeric/status checks) before
    showing them to a user. Complete elements are cached after a clean or
    length-truncated response; timeouts, idle stalls and transport errors are
    never cached, so a transient failure cannot pin partial advice in the
    cache for the full TTL.
    """
    try:
        backend = _backend()
        if backend == "groq" and not api_key:
            yield {
                "type": "done", "elements": 0, "reason": "missing_key",
                "truncated": False, "cached": False,
            }
            return
        if timeout is None:
            timeout = _stream_timeout_seconds()

        prompt = build_prompt(analysis, lang=lang, selection=selection)
        key = recommendation_cache.make_key(
            prompt, lang, _cache_model(backend))
        cached = recommendation_cache.get(key)
        if cached:
            for element, record in cached.items():
                yield {"type": "element", "element": element, "record": record}
            yield {
                "type": "done", "elements": len(cached), "reason": None,
                "truncated": False, "cached": True,
            }
            return

        from langchain.schema import HumanMessage, SystemMessage

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        llm = get_llm_client(api_key=api_key, timeout=timeout, backend=backend)
        _bump("llm_calls")

        seen = {}
        buffer = ""
        truncated = False
        reason = None
        idle = _stream_idle_seconds()
        stream = llm.astream(messages).__aiter__()
        try:
            async with asyncio.timeout(timeout):
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            stream.__anext__(), timeout=idle)
                    except StopAsyncIteration:
                        break
                    except TimeoutError:
                        truncated = True
                        reason = "idle_timeout"
                        break
                    metadata = getattr(chunk, "response_metadata", None) or {}
                    if (
                        metadata.get("finish_reason") == "length"
                        or metadata.get("done_reason") == "length"
                    ):
                        truncated = True
                    text = _chunk_text(chunk)
                    if not text:
                        continue
                    buffer += text
                    for element, record in parse_llm_json_partial(buffer).items():
                        if element in seen:
                            continue
                        seen[element] = record
                        yield {
                            "type": "element", "element": element,
                            "record": record,
                        }
        except TimeoutError:
            truncated = True
            reason = "timeout"
        except Exception as exc:
            reason = "stream_error"
            logger.info("llm_stream_error error=%s", type(exc).__name__)
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                try:
                    await aclose()
                except Exception:
                    pass

        stats = {}
        parse_llm_json_partial(buffer, stats=stats)
        if stats.get("rejected"):
            _bump("llm_contract_rejects", stats["rejected"])

        if seen:
            if reason is None:
                recommendation_cache.put(key, seen)
            if truncated:
                _bump("llm_truncations")
                _bump("llm_elements_recovered", len(seen))
            _log_call(
                "ok", elements=len(seen),
                recovered=len(seen) if truncated else 0,
                truncated=truncated,
            )
        else:
            _bump("llm_parse_failures")
            if truncated:
                _bump("llm_truncations")
            _log_call("parse_failed", truncated=truncated)
            if reason is None:
                reason = "truncated" if truncated else "parse_failed"

        yield {
            "type": "done", "elements": len(seen), "reason": reason,
            "truncated": truncated, "cached": False,
        }
    except Exception as exc:
        logger.info("llm_stream_unavailable error=%s", type(exc).__name__)
        yield {
            "type": "done", "elements": 0, "reason": "unavailable",
            "truncated": False, "cached": False,
        }
