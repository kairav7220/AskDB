import re
import time

import altair as alt
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="AskDB — text to SQL",
    page_icon=":material/query_stats:",
    layout="wide",
)

# Hide scrollbars everywhere for a clean, app-like feel. Content still scrolls
# (mouse wheel / trackpad), only the visible bars are removed.
st.markdown(
    """
    <style>
    ::-webkit-scrollbar { display: none; }
    * { scrollbar-width: none; }
    </style>
    """,
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------------
# Backend (cached so DB + LLM init happens exactly once)
# --------------------------------------------------------------------------
@st.cache_resource
def get_backend():
    import backend

    return backend


def check_database():
    try:
        return True, get_backend().list_tables(), None
    except Exception as exc:
        return False, [], str(exc)


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
if "messages" not in st.session_state:
    st.session_state.messages = []

if "bench_df" not in st.session_state:
    st.session_state.bench_df = None

SUGGESTIONS = [
    "What was the budget of Product 12?",
    "List all customer names from the customers table.",
    "Find the name and state of all regions in the regions table.",
    "What is the name of the customer with Customer Index = 1?",
    "Show product names and their 2017 budgets.",
]

MAX_PREVIEW_ROWS = 10

# Chat panel height: the conversation scrolls inside this bounded area while
# the input stays pinned to the bottom of the screen.
CHAT_HEIGHT = 560

# Chat avatars — custom branded marks (static/*.svg): teal prompt-glyph for the
# assistant, slate person glyph for the user.
AVATARS = {
    "user": "static/user_avatar.svg",
    "assistant": "static/bot_avatar.svg",
}

# Theme primary (see .streamlit/config.toml) — used so both history trend
# charts share exactly the same line colour.
LINE_COLOR = "#0F766E"


def safe_mean(series):
    vals = pd.to_numeric(pd.Series(series), errors="coerce").dropna()
    return float(vals.mean()) if len(vals) else 0.0


def trend_chart(data, y_col, title, fmt):
    chart_data = data.dropna(subset=[y_col])
    if chart_data.empty:
        return None
    return (
        alt.Chart(chart_data)
        .mark_line(point=True, strokeWidth=2.5)
        .properties(title=title, height=280)
        .encode(
            x=alt.X("timestamp:T", title="Run time"),
            y=alt.Y(f"{y_col}:Q", scale=alt.Scale(zero=False), title=None),
            color=alt.value(LINE_COLOR),
            tooltip=[
                alt.Tooltip("timestamp:T", title="Run"),
                alt.Tooltip(f"{y_col}:Q", title=title, format=fmt),
            ],
        )
    )


def build_history(messages):
    pairs = []
    for i, m in enumerate(messages):
        if m.get("role") == "user":
            q = m.get("content", "")
            sql = ""
            nxt = messages[i + 1] if i + 1 < len(messages) else {}
            if nxt.get("role") == "assistant" and nxt.get("sql"):
                sql = nxt["sql"]
            pairs.append({"question": q, "sql": sql})
    return pairs[-5:]


def result_to_df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


def stream_answer(i):
    """Stream the answer for the pending assistant message at index ``i`` into
    the currently-open chat bubble, then persist the results on the message.

    Renders nothing itself except inside the bubble it is called within, so
    every message is drawn exactly once, always above the chat input.
    """
    msg = st.session_state.messages[i]
    question = ""
    for m in reversed(st.session_state.messages[:i]):
        if m.get("role") == "user":
            question = m.get("content", "")
            break

    backend = get_backend()
    history = build_history(st.session_state.messages[: i - 1])
    t0 = time.perf_counter()
    try:
        stream_box = st.empty()
        streamed = stream_box.write_stream(backend.stream_sql(question, history))
        stream_box.empty()
        st.code(streamed, language="sql", wrap_lines=True, height="content")
        with st.spinner("Running query and writing the answer…"):
            sql, rows, cols, retries = backend.execute_sql(
                streamed, question, history
            )
            df = result_to_df(rows, cols)
            answer = backend.explain_result(question, sql, rows)
        elapsed = time.perf_counter() - t0
        st.write(answer)
        if retries:
            st.caption(
                f"Auto-fixed after {retries} retr{'y' if retries == 1 else 'ies'}"
            )
        if len(df):
            st.dataframe(df.head(MAX_PREVIEW_ROWS), hide_index=True)
            if len(df) > MAX_PREVIEW_ROWS:
                st.caption(
                    f"Preview only — first {MAX_PREVIEW_ROWS} of {len(df)} rows."
                )
        else:
            st.caption("The query ran successfully but returned no rows.")
        st.caption(
            f"{len(df)} row{'s' if len(df) != 1 else ''} returned in {elapsed:.2f}s"
        )
    except Exception as exc:
        st.error(f"Could not run that question: {exc}")
        msg.pop("pending", None)
        msg["error"] = str(exc)
        return

    msg.pop("pending", None)
    msg.update(
        {
            "answer": answer,
            "sql": sql,
            "df": df,
            "rows": len(df),
            "retried": retries,
            "elapsed": elapsed,
        }
    )


def render_assistant(msg):
    if "error" in msg:
        st.error(f"Could not run that question: {msg['error']}")
        return
    if msg.get("answer"):
        st.write(msg["answer"])
    st.code(msg.get("sql", ""), language="sql", wrap_lines=True, height="content")
    if msg.get("retried"):
        n = msg["retried"]
        st.caption(f"Auto-fixed after {n} retr{'y' if n == 1 else 'ies'}")
    rows = msg.get("rows", 0)
    if rows:
        st.dataframe(msg["df"].head(MAX_PREVIEW_ROWS), hide_index=True)
        if rows > MAX_PREVIEW_ROWS:
            st.caption(f"Preview only — first {MAX_PREVIEW_ROWS} of {rows} rows.")
    else:
        st.caption("The query ran successfully but returned no rows.")
    st.caption(
        f"{rows} row{'s' if rows != 1 else ''} returned in {msg.get('elapsed', 0.0):.2f}s"
    )


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
def render_ask_page():
    # Onboarding suggestion chips — shown only while the chat is still empty.
    selected = None
    if not st.session_state.messages:
        st.caption("Start with an example, or type your own question below.")
        selected = st.pills(
            "Try asking",
            SUGGESTIONS,
            key="suggestions",
            label_visibility="collapsed",
        )

    # Bounded chat panel above the pinned input. It scrolls internally and
    # auto-scrolls to the newest message; the scrollbar is hidden via CSS.
    if st.session_state.messages:
        chat = st.container(height=CHAT_HEIGHT, border=True, autoscroll=True)
        for i, msg in enumerate(st.session_state.messages):
            with chat.chat_message(msg["role"], avatar=AVATARS.get(msg["role"])):
                if msg["role"] == "user":
                    st.write(msg["content"])
                elif msg.get("pending"):
                    stream_answer(i)
                else:
                    render_assistant(msg)

    # Called directly in the main body, so Streamlit pins it to the bottom of
    # the screen — the input never moves. It also consumes pill picks.
    prompt = st.chat_input("Ask the sales database…", key="ask_input")
    if selected:
        prompt = selected
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        st.session_state.messages.append({"role": "assistant", "pending": True})
        st.rerun()


def render_schema_page():
    backend = get_backend()

    db_ok_t, db_tables_t, _ = check_database()
    tables = db_tables_t if db_ok_t else []
    if not tables:
        st.warning(
            "No tables found — the database is unavailable.",
            icon=":material/database_off:",
        )
    else:
        col_table, col_cols = st.columns([1, 2])
        with col_table:
            chosen = st.radio("Table", tables, index=0)
            rowcount = backend.count_rows(chosen)
            st.caption(f"**{rowcount}** rows")

        with col_cols:
            cols = pd.DataFrame(backend.table_columns(chosen))
            st.dataframe(cols, hide_index=True)

    st.caption("Schema only — row data stays in the database and never leaves it.")


def render_evaluate_page():
    st.caption(
        "Five fixed questions are pushed through the pipeline and scored by RAGAS — "
        "context precision (retrieval relevance) and a 1–5 helpfulness rubric. "
        "Each run is saved so you can watch scores change over time. Uses live "
        "LLM calls; allow a couple of minutes."
    )
    if st.button("Run benchmark", type="primary", icon=":material/speed:"):
        with st.status("Running benchmark…", expanded=True) as status:
            try:
                status.update(
                    label="Generating SQL for 5 questions, then scoring with RAGAS…"
                )
                result = get_backend().evaluate_ragas()
                bench_df = result.to_pandas()
                status.update(label="Benchmark complete", state="complete", expanded=False)
            except Exception as exc:
                status.update(label="Benchmark failed", state="error")
                st.error(f"Benchmark error: {exc}")
                st.stop()
        backend = get_backend()
        backend.save_eval_run(bench_df)
        st.session_state.bench_df = bench_df
        st.rerun()

    if st.session_state.bench_df is not None:
        df = st.session_state.bench_df
        metric_cols = [c for c in df.columns if c in ("context_precision", "helpfulness")]
        means = {c: safe_mean(df[c]) for c in metric_cols}

        with st.container(border=True):
            st.markdown("**Latest run — averages**")
            with st.container(horizontal=True):
                st.metric(
                    "Context precision",
                    f"{means.get('context_precision', 0.0):.3f}",
                    border=True,
                )
                st.metric(
                    "Helpfulness",
                    f"{means.get('helpfulness', 0.0):.2f} / 5",
                    border=True,
                )

            st.markdown("**Per-question scores — latest run**")
            detail = pd.DataFrame(
                [
                    {
                        "question": r.get("user_input", ""),
                        "generated SQL": r.get("response", ""),
                        "context precision": r.get("context_precision", ""),
                        "helpfulness": r.get("helpfulness", ""),
                    }
                    for _, r in df.iterrows()
                ]
            )
            score_cols = [c for c in detail.columns if c in ("context precision", "helpfulness")]
            chart_df = detail.copy()
            chart_df.insert(0, "q", [f"Q{i + 1}" for i in range(len(chart_df))])
            long = chart_df.melt(
                id_vars=["q", "question"],
                value_vars=score_cols,
                var_name="metric",
                value_name="score",
            )
            st.altair_chart(
                alt.Chart(long)
                .mark_bar()
                .encode(
                    x=alt.X("q:N", title=None),
                    xOffset="metric:N",
                    y=alt.Y("score:Q", title="Score"),
                    color=alt.Color(
                        "metric:N",
                        legend=alt.Legend(title=None, orient="top"),
                        scale=alt.Scale(
                            domain=score_cols,
                            range=["#0F766E", "#3BA98C"],
                        ),
                    ),
                    tooltip=[
                        alt.Tooltip("question:N", title="Question"),
                        alt.Tooltip("metric:N", title="Metric"),
                        alt.Tooltip("score:Q", title="Score", format=".3f"),
                    ],
                )
                .properties(height=320),
                width="stretch",
            )
            st.dataframe(
                detail,
                hide_index=True,
                column_config={
                    "question": st.column_config.TextColumn("Question", width="medium"),
                    "generated SQL": st.column_config.TextColumn("Generated SQL", width="large"),
                    "context precision": st.column_config.NumberColumn(
                        "Context precision", format="%.3f"
                    ),
                    "helpfulness": st.column_config.NumberColumn(
                        "Helpfulness", format="%.2f"
                    ),
                },
            )

    hist_head, hist_clear = st.columns([18, 2], vertical_alignment="center")
    with hist_head:
        st.markdown("#### Score history")
    with hist_clear:
        if st.button("Clear history", icon=":material/delete_sweep:", key="clear_history"):
            get_backend().clear_eval_history()
            st.session_state.bench_df = None
            st.rerun()
    st.caption(
        "Every run is recorded here, newest last. Scores are per-run averages "
        "of the five benchmark questions. Trends draw after the second run."
    )
    hist = get_backend().load_eval_history()
    if hist:
        hist_df = pd.DataFrame(hist).replace({float("nan"): None})
        hist_df["timestamp"] = pd.to_datetime(hist_df["timestamp"])
        st.dataframe(
            hist_df,
            hide_index=True,
            column_config={
                "timestamp": st.column_config.DatetimeColumn(
                    "Run at", format="MMM DD, YYYY · HH:mm"
                ),
                "context_precision": st.column_config.NumberColumn(
                    "Context precision", format="%.3f"
                ),
                "helpfulness": st.column_config.NumberColumn("Helpfulness", format="%.2f"),
            },
        )

        c1, c2 = st.columns(2)
        with c1:
            chart = trend_chart(hist_df, "context_precision", "Context precision", ".3f")
            if chart is None:
                st.caption("No context precision scores recorded yet.")
            else:
                st.altair_chart(chart, width="stretch")
        with c2:
            chart = trend_chart(hist_df, "helpfulness", "Helpfulness", ".1f")
            if chart is None:
                st.caption("No helpfulness scores recorded yet.")
            else:
                st.altair_chart(chart, width="stretch")
    else:
        st.caption("No past runs yet — run the benchmark to record the first one.")


def render_database_page():
    backend = get_backend()

    st.markdown("#### Import a CSV as a new table")
    st.caption(
        "The table is written straight into the database and appears in the "
        "Schema page immediately."
    )
    csv_file = st.file_uploader("Choose a CSV file", type=["csv"], key="csv_upload")
    table_name = st.text_input("Table name", placeholder="e.g. new_sales")

    if st.button("Create table", type="primary", icon=":material/upload:"):
        if csv_file is None:
            st.warning("Choose a CSV file first.")
        elif not (table_name or "").strip():
            st.warning("Give the table a name.")
        else:
            name = re.sub(r"\W", "_", table_name.strip())
            try:
                n = backend.load_csv_to_table(csv_file, name)
                st.success(f"Created table `{name}` with {n} rows.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not import CSV: {exc}")

    st.markdown("#### Rebuild from source CSVs")
    st.caption(
        "Recreates the six seeded tables from the files in `Data_CSV/`. "
        "User-added tables are left untouched."
    )
    if st.button("Rebuild database", icon=":material/refresh:"):
        try:
            backend.create_database()
            backend.reload_database()
            st.success("Database rebuilt from `Data_CSV/`.")
            st.rerun()
        except Exception as exc:
            st.error(f"Rebuild failed: {exc}")

    st.markdown("**Delete only the tables you added** — seeded tables are protected.")
    all_tables = backend.list_tables()
    seeded = set(backend.CSV_TABLES.values())
    added = [t for t in all_tables if t not in seeded]
    if added:
        for t in added:
            c1, c2 = st.columns([3, 1], vertical_alignment="center")
            with c1:
                st.write(t)
            with c2:
                if st.button("Delete", key=f"del_{t}", icon=":material/delete:"):
                    try:
                        backend.drop_table(t)
                        st.success(f"Deleted `{t}`.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete `{t}`: {exc}")
    else:
        st.caption("No user-added tables yet — upload a CSV above and it will appear here.")


# --------------------------------------------------------------------------
# Header
# --------------------------------------------------------------------------
st.title("AskDB")
st.caption(
    "Ask a sales question in plain English. It becomes a SQL query, runs against "
    "the database, and hands back an answer with the rows."
)

db_ok, db_tables, db_error = check_database()
if db_ok:
    st.badge(
        f"{len(db_tables)} tables",
        icon=":material/database:",
        color="green",
    )
else:
    st.badge(f"database unavailable", icon=":material/error:", color="red")

# --------------------------------------------------------------------------
# Sidebar (app info only)
# --------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### About")
    st.caption(
        "AskDB turns natural language into SQL using LangChain (Gemini with a "
        "Groq fallback) and runs it against the database. Queries are "
        "read-only — only `SELECT`/`WITH` is allowed."
    )
    if st.button("Clear chat", icon=":material/delete:"):
        st.session_state.messages = []
        st.rerun()

# --------------------------------------------------------------------------
# Page navigation.
#
# This replaces st.tabs: st.chat_input is only pinned to the bottom of the app
# when it is called directly in the main body — inside a tab/container it
# becomes inline and floats with the content. So the chat page renders here at
# the top level, and the input stays fixed at the bottom of the screen.
# --------------------------------------------------------------------------
PAGES = {
    "Ask the data": ":material/chat:",
    "Schema": ":material/table_chart:",
    "Evaluate": ":material/speed:",
    "Database": ":material/upload:",
}
page = st.segmented_control(
    "Page",
    list(PAGES),
    default="Ask the data",
    key="page_nav",
    label_visibility="collapsed",
    format_func=lambda p: f"{PAGES[p]} {p}",
)

if page == "Ask the data":
    render_ask_page()
elif page == "Schema":
    render_schema_page()
elif page == "Evaluate":
    render_evaluate_page()
else:
    render_database_page()
