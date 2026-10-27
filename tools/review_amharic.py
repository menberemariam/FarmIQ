"""Development-time Amharic translation QA.

Reviews every Amharic recommendation template (and optionally the UI strings)
with the Groq LLM: it checks grammar, register, terminology and placeholder
integrity, and prints a per-item verdict. Requires GROQ_API_KEY.

Run:  python3 tools/review_amharic.py [--all]
Output is appended to docs/amharic_review.md.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain.schema import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv()

from ML import engine  # noqa: E402
from ML.engine import analyze_soil, recommend_soil  # noqa: E402

REVIEW_PROMPT = """You are a native Amharic language and agronomy expert reviewing a translation.

English source:
---START---
{en}
---END---

Amharic translation:
---START---
{am}
---END---

Check ALL of the following and reply in English:
1. Grammar and natural word order (Amharic is SOV) — is it fluent and correct?
2. Register: formal extension-leaflet style (polite imperative / impersonal "it is recommended")?
3. Terminology: are the Amharic terms correct (urea=ዩሪያ, soil=አፈር, lime=ሎሚ, fertilizer=ማዳበሪያ...)?
4. Are the numbers and units (kg/ha, t/ha) identical to the English source?
5. Is the sentence-ending punctuation ። used correctly?

Reply strictly as:
VERDICT: PASS | MINOR | FAIL
ISSUES: (bullet list, or "none")
SUGGESTED_FIX: (corrected Amharic text if FAIL/MINOR, else "none")
"""


def _llm():
    key = os.getenv("GROQ_API_KEY")
    if not key:
        print("GROQ_API_KEY not set — cannot run the review.")
        sys.exit(1)
    return ChatGroq(groq_api_key=key, model_name="llama-3.3-70b-versatile")


def review_items(llm, items):
    """items: list of (name, en_text, am_text). Returns list of verdict dicts."""
    messages = [
        SystemMessage(content="You are a strict Amharic translation reviewer."),
        HumanMessage(content=REVIEW_PROMPT.format(en=items[0][1], am=items[0][2])),
    ]
    return llm.invoke(messages).content


def main():
    include_ui = "--all" in sys.argv
    llm = _llm()

    items = []
    analysis = analyze_soil(25, 10, 40, 5.5, crop="rice")
    for en, am in zip(
        recommend_soil(analysis, lang="en"), recommend_soil(analysis, lang="am")
    ):
        items.append((en["element"], en["solution"], am["solution"]))

    if include_ui:
        import i18n

        en_ui = i18n.STRINGS["en"]
        am_ui = i18n.STRINGS["am"]
        for key in en_ui:
            items.append((f"ui:{key}", en_ui[key], am_ui.get(key, "")))

    out = [f"# Amharic Review — {__import__('datetime').date.today()}"]
    print(f"Reviewing {len(items)} items with Groq...")
    for name, en, am in items:
        if not am:
            out.append(f"\n## {name}\nVERDICT: FAIL — missing translation")
            continue
        verdict = review_items(llm, [(name, en, am)])
        out.append(f"\n## {name}\n{verdict}")

    report = "\n".join(out)
    docs_dir = PROJECT_ROOT / "docs"
    docs_dir.mkdir(exist_ok=True)
    (docs_dir / "amharic_review.md").write_text(report)
    print(f"Report written to docs/amharic_review.md")


if __name__ == "__main__":
    main()
