import json
import os

import joblib
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from langchain.schema import HumanMessage, SystemMessage
from langchain_groq import ChatGroq

import config
import db
from i18n import LANGUAGES, translate as t
from ML.engine import (
    analyze_soil,
    available_crops,
    build_prompt,
    merge_recommendations,
    parse_llm_json,
    recommend_soil,
)

load_dotenv()
db.init_db()


@st.cache_resource
def load_models():
    soil_rf = joblib.load(config.SOIL_MODEL_PKL)
    crop_rf = joblib.load(config.CROP_MODEL_PKL)
    return soil_rf, crop_rf


def is_out_of_training_range(N, P, K, ph):
    warnings = []
    for name, value in (("N", N), ("P", P), ("K", K), ("ph", ph)):
        lo, hi = config.UI_INPUT_RANGES[name]
        if value < lo or value > hi:
            warnings.append(
                t(lang, "out_of_range", value=f"{name}={value}", lo=lo, hi=hi)
            )
    return warnings


def get_llm_recommendations(analysis, api_key):
    if not api_key:
        return None
    prompt = build_prompt(analysis)
    messages = [
        SystemMessage(
            content="You are a JSON API. Always respond with ONLY valid JSON. "
            "No text, no explanations, no code fences."
        ),
        HumanMessage(content=prompt),
    ]
    llm = ChatGroq(groq_api_key=api_key, model_name="llama-3.3-70b-versatile")
    for attempt in range(2):
        try:
            response = llm.invoke(messages)
            return parse_llm_json(response.content)
        except Exception:
            if attempt == 0:
                continue
            raise
    return None


def top_crops(crop_rf, row):
    probs = crop_rf.predict_proba(row)[0]
    order = probs.argsort()[::-1][:3]
    return [
        {"crop": crop_rf.classes_[i], "confidence": float(probs[i])} for i in order
    ]


# ---------------- UI ----------------

lang = st.sidebar.selectbox(t("en", "language"), options=list(LANGUAGES), format_func=lambda k: LANGUAGES[k])

st.title(t(lang, "title"))
st.subheader(t(lang, "subtitle"))

crop = st.selectbox(
    t(lang, "crop"),
    options=["generic"] + available_crops(),
    format_func=lambda c: t(lang, "crop_generic") if c == "generic" else c.title(),
)

N = st.number_input(t(lang, "nitrogen"), min_value=0, max_value=160, value=40)
P = st.number_input(t(lang, "phosphorus"), min_value=0, max_value=160, value=20)
K = st.number_input(t(lang, "potassium"), min_value=0, max_value=220, value=110)
ph = st.number_input(t(lang, "ph"), min_value=0.0, max_value=14.0, value=6.5, step=0.1)

crop_label = None if crop == "generic" else crop

if st.button(t(lang, "predict")):
    soil_rf, crop_rf = load_models()

    for msg in is_out_of_training_range(N, P, K, ph):
        st.warning(msg)

    user_input = pd.DataFrame({"N": [N], "P": [P], "K": [K], "ph": [ph]})
    predicted_data = soil_rf.predict(user_input)[0]
    st.success(t(lang, "predicted", status=t(lang, predicted_data.lower())))

    analysis = analyze_soil(N, P, K, ph, crop=crop_label)
    deterministic = recommend_soil(analysis)

    api_key = os.getenv("GROQ_API_KEY")
    llm_recs = None
    try:
        llm_recs = get_llm_recommendations(analysis, api_key)
    except Exception:
        llm_recs = None

    if llm_recs is None:
        st.info(t(lang, "api_unavailable"))
        recommendations = deterministic
    else:
        recommendations = merge_recommendations(llm_recs, deterministic)

    st.subheader(t(lang, "recommendations"))
    for rec in recommendations:
        st.write(f"## {rec['element']}: {t(lang, rec['status'])}")
        st.write(f"{t(lang, 'solution')}: {rec['solution']}")
        if rec["source"] == "llm":
            st.caption(t(lang, "ai_generated"))
        else:
            st.caption(t(lang, "calculated"))

    crop_recs = top_crops(
        crop_rf, pd.DataFrame([{"N": N, "P": P, "K": K, "ph": ph}])
    )

    st.session_state["last_report"] = {
        "N": N, "P": P, "K": K, "ph": ph,
        "crop": crop_label,
        "fertility_status": predicted_data,
        "recommendations": recommendations,
        "crops": crop_recs,
    }

# ---------------- Save + History ----------------

with st.sidebar.expander(t(lang, "history")):
    plot_input = st.text_input(t(lang, "plot"), key="plot_input")

    if "last_report" in st.session_state and plot_input.strip():
        if st.button(t(lang, "save_report")):
            rep = st.session_state["last_report"]
            db.save_report(
                plot_input, rep["N"], rep["P"], rep["K"], rep["ph"],
                rep["fertility_status"], rep["recommendations"],
                crop=rep["crop"], crops=rep["crops"],
            )
            st.success(t(lang, "save_ok"))
    elif "last_report" in st.session_state:
        st.caption(t(lang, "save_missing_plot"))

    plots = db.list_plots()
    if not plots:
        st.write(t(lang, "history_none"))
    else:
        plot_names = [p["plot"] for p in plots]
        selected_plot = st.selectbox(t(lang, "plot_select"), plot_names, key="plot_select")
        reports = db.list_reports(plot=selected_plot, limit=5)

        table_rows = []
        for r in reports:
            status = t(lang, r["fertility_status"].lower())
            table_rows.append(
                {
                    t(lang, "report_date"): r["created_at"][:10],
                    t(lang, "report_fertility"): status,
                    t(lang, "n"): r["N"],
                    t(lang, "p"): r["P"],
                    t(lang, "k"): r["K"],
                    "pH": r["ph"],
                }
            )
        st.table(pd.DataFrame(table_rows))

        tracking = db.latest_two(selected_plot)
        if tracking:
            st.markdown(t(lang, "improvement"))
            delta_texts = {
                "N": f"{tracking['deltas']['N']:+.1f}",
                "P": f"{tracking['deltas']['P']:+.1f}",
                "K": f"{tracking['deltas']['K']:+.1f}",
                "ph": f"{tracking['deltas']['ph']:+.2f}",
            }
            old_new = t(lang, "status_change", old=t(lang, tracking["old_status"].lower()),
                        new=t(lang, tracking["new_status"].lower()))
            st.write(
                " | ".join(f"{k}: {delta_texts[k]} kg/ha" for k in ("N", "P", "K"))
                + f" | pH: {delta_texts['ph']}"
                + (" | " + old_new if tracking["status_changed"] else "")
            )
            trend_df = pd.DataFrame(
                [
                    {
                        "date": r["created_at"][:10],
                        "N": r["N"], "P": r["P"], "K": r["K"], "pH": r["ph"],
                    }
                    for r in reports[::-1]
                ]
            )
            st.line_chart(trend_df.set_index("date"))
        else:
            st.caption(t(lang, "no_previous"))

with st.expander(t(lang, "crop_rec")):
    st.caption(t(lang, "crop_rec_hint"))
    crop_df = pd.read_csv(config.CROP_DATA_CSV)
    temperature = st.number_input(
        t(lang, "temperature"), min_value=0.0, max_value=50.0,
        value=float(crop_df["temperature"].median()),
    )
    humidity = st.number_input(
        t(lang, "humidity"), min_value=0.0, max_value=100.0,
        value=float(crop_df["humidity"].median()),
    )
    rainfall = st.number_input(
        t(lang, "rainfall"), min_value=0.0, max_value=500.0,
        value=float(crop_df["rainfall"].median()),
    )

    if st.button(t(lang, "get_crop_rec")):
        soil_rf, crop_rf = load_models()
        crop_input = pd.DataFrame(
            [{"N": N, "P": P, "K": K, "temperature": temperature,
              "humidity": humidity, "ph": ph, "rainfall": rainfall}]
        )[config.CROP_FEATURES]
        for rank, rec in enumerate(top_crops(crop_rf, crop_input), start=1):
            st.write(f"{rank}. **{rec['crop']}** — {rec['confidence'] * 100:.1f}% {t(lang, 'confidence')}")
            st.progress(min(rec["confidence"], 1.0))

st.caption(t(lang, "disclaimer"))
