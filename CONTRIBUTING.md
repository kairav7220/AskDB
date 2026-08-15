# Contributing

Thanks for contributing to AskDB! This guide covers setup, development, testing, and the rules for a clean PR.

## Setup

```bash
git clone https://github.com/kairav7220/AskDB.git
cd AskDB
python -m venv .venv
.venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in your keys:

```env
GOOGLE_API_KEY="AIza..."     # primary LLM
GROQ_API_KEY="gsk_..."       # optional fallback
```

Run the app:

```bash
streamlit run frontend.py
```

Open `http://localhost:8501`. The app has four pages: **Ask the data**, **Schema**, **Evaluate**, **Database**.

## Where things live

| Concern | File |
|---|---|
| UI, pages, layout, session state | `frontend.py` |
| LLM chains, SQL generation/fix/execution | `backend.py` |
| Benchmark questions & references | `TEST_QUESTIONS` / `TEST_REFERENCES` in `backend.py` |
| RAGAS evaluation | `backend.evaluate_ragas()` |
| Database seeding from CSVs | `backend.ensure_database()` / `create_database()` |
| Eval history (save / load / clear) | `backend.save_eval_run()` / `load_eval_history()` / `clear_eval_history()` |
| Vertex AI import shim (ragas) | `vertexai_shim.py` — do not remove |

## Testing changes

- **Chat flow:** type a question on **Ask the data**; confirm the SQL streams, the answer renders, and the input stays pinned to the bottom.
- **Evaluation:** press **Run benchmark** on **Evaluate**; confirm scores are saved (`eval_history.json`) and the trend charts draw after the second run.
- **Clear history:** press **Clear history** next to the Score history heading; confirm the table, charts, and latest-run section all disappear.
- **Database:** import a CSV on the **Database** page, then delete it; confirm seeded tables cannot be deleted.

## PR Guidelines

- One feature/fix per PR.
- Include RAGAS evaluation results if changing the LLM, prompt, or evaluation setup.
- Update `requirements.txt` if adding dependencies.
- Keep the app runnable on Streamlit Community Cloud — no hardcoded credentials.

## Code Style

- `ruff` for linting (defaults are fine).
- Type hints on function signatures.
- No hardcoded secrets — always use `.env` locally and `st.secrets` on the cloud.
- Keep the UI free of visible scrollbars (global CSS in `frontend.py` hides them).
- Preserve the read-only guard in `validate_read_only()` — every query must stay `SELECT`/`WITH`.

## Do not commit

- `.env`, `.streamlit/secrets.toml`
- `.venv/`, `__pycache__/`
- `*.db`, `eval_history.json`, `data_dump/`, `create_db.py`

See `.gitignore`.

## License

MIT © [kairav7220](https://github.com/kairav7220)
