import math
import os, re, json
from datetime import datetime

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import inspect, text

import vertexai_shim
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_community.utilities import SQLDatabase
from langchain_huggingface.embeddings import HuggingFaceEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq

from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas import evaluate
from ragas.metrics import RubricsScore, ContextPrecision
from ragas.dataset_schema import SingleTurnSample, EvaluationDataset
load_dotenv()

CSV_TABLES = {
    'Data_CSV/Customers.csv': 'Customers',
    'Data_CSV/Sales_order.csv': 'sales_order',
    'Data_CSV/Products.csv': 'Products',
    'Data_CSV/Regions.csv': 'Regions',
    'Data_CSV/State_Regions.csv': 'State_Regions',
    'Data_CSV/2017_Budgets.csv': 'Budgets_2017'
}

TEST_QUESTIONS = [
    'What was the budget of Product 12',
    'What are the names of all products in the products table?',
    'List all customer names from the customers table.',
    'Find the name and state of all regions in the regions table.',
    'What is the name of the customer with Customer Index = 1',
]

TEST_REFERENCES = [
    "SELECT \"2017 Budgets\" FROM \"Budgets_2017\" WHERE \"Product Name\" = 'Product 12';",
    'SELECT "Product Name" FROM "Products";',
    'SELECT "Customer Names" FROM "Customers";',
    'SELECT name, state FROM "Regions";',
    'SELECT "Customer Names" FROM "Customers" WHERE "Customer Index" = 1'
]

EVAL_HISTORY_FILE = 'eval_history.json'
MAX_RETRIES = 3


def get_engine():
    return st.connection('app_db', type='sql').engine


def list_tables():
    return sorted(inspect(get_engine()).get_table_names())


def count_rows(table_name):
    with get_engine().connect() as conn:
        return conn.execute(text(f'SELECT COUNT(*) FROM "{table_name}"')).fetchone()[0]


def table_columns(table_name):
    cols = []
    for col in inspect(get_engine()).get_columns(table_name):
        cols.append({
            "column": col["name"],
            "type": str(col["type"]),
            "not null": not col.get("nullable", True),
            "primary key": col.get("primary_key", False),
        })
    return cols

SQL_TEMPLATE = """Based on the table schema below and the recent conversation history, write a SQL query that would answer the user's question:
Remember: Only provide me the raw SQL query. Do not include markdown blocks like ```sql.
Provide the query in a single line without line breaks. The query must be read-only (SELECT or WITH only).

Use the EXACT table and column names shown in the schema. Names are case-sensitive: if a table or column name is not plain lowercase (for example "Customers", "Regions", "Customer Names", "2017 Budgets"), wrap it in double quotes, e.g. FROM "Customers", "Customer Names".

Table Schema: {schema}
{history}

Question: {question}
SQL Query:"""

FIX_TEMPLATE = """The SQL query below failed when run against the database.
Table Schema: {schema}
Question: {question}
Failed SQL: {failed_sql}
Error: {error}

Return a corrected SQL query that fixes the problem. Only provide the raw SQL query.
Do not include markdown blocks like ```sql. Provide the query in a single line without line breaks.
The query must be read-only (SELECT or WITH only).
Use the EXACT table and column names from the schema — names are case-sensitive, so wrap any name that is not plain lowercase in double quotes (e.g. FROM "Customers", "Customer Names").
Corrected SQL:"""

EXPLAIN_TEMPLATE = """Answer the user's question in plain English, based on the query and its result.
Question: {question}
SQL: {sql}
Result rows: {result}

Give a short, direct answer in 1-2 sentences. Do not mention SQL, column names, or the query result format.
Answer:"""


def ensure_database():
    engine = get_engine()
    existing = set(inspect(engine).get_table_names())
    for csv_file, table in CSV_TABLES.items():
        if table not in existing and os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            df.to_sql(table, engine, if_exists='replace', index=False)


def create_database():
    engine = get_engine()
    for table in set(CSV_TABLES.values()):
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))
    for csv_file, table in CSV_TABLES.items():
        if os.path.exists(csv_file):
            df = pd.read_csv(csv_file)
            df.to_sql(table, engine, if_exists='replace', index=False)


def reload_database():
    global db
    db = SQLDatabase(get_engine(), sample_rows_in_table_info=3)
    return db


def _secret(name):
    try:
        return os.getenv(name) or os.getenv(name.lower()) or st.secrets.get(name) or None
    except Exception:
        return os.getenv(name) or os.getenv(name.lower()) or None


def init():
    ensure_database()
    db = SQLDatabase(get_engine(), sample_rows_in_table_info=3)
    primary = ChatGoogleGenerativeAI(model='gemini-flash-lite-latest', temperature=0.0,
                                     api_key=_secret('GOOGLE_API_KEY'))
    fallback = ChatGroq(model='llama-3.3-70b-versatile', temperature=0.0,
                        api_key=_secret('GROQ_API_KEY'))
    llm = primary.with_fallbacks([fallback])

    sql_prompt = ChatPromptTemplate.from_template(SQL_TEMPLATE)
    sql_chain = (
        RunnablePassthrough.assign(schema=lambda _: get_table_info())
        | sql_prompt
        | llm
        | StrOutputParser()
    )
    fix_chain = ChatPromptTemplate.from_template(FIX_TEMPLATE) | llm | StrOutputParser()
    explain_chain = ChatPromptTemplate.from_template(EXPLAIN_TEMPLATE) | llm | StrOutputParser()

    embeddings = HuggingFaceEmbeddings(model_name='sentence-transformers/all-MiniLM-L6-v2')
    evaluator_llm = LangchainLLMWrapper(primary)
    evaluator_embeddings = LangchainEmbeddingsWrapper(embeddings)

    return db, sql_chain, fix_chain, explain_chain, evaluator_llm
db, sql_chain, fix_chain, explain_chain, evaluator_llm = init()


def get_table_info():
    return db.get_table_info()


def _clean_sql(raw):
    if '```' in raw:
        match = re.search(r'```(?:sql)?\s*(.*?)\s*```', raw, re.DOTALL | re.IGNORECASE)
        sql = match.group(1).strip() if match else raw
    else:
        sql = raw
    return ' '.join(sql.split())


def _history_block(history):
    history = history or []
    if not history:
        return ''
    lines = []
    for turn in history[-5:]:
        q = str(turn.get('question', '')).strip()
        s = str(turn.get('sql', '')).strip()
        if q:
            lines.append(f'Q: {q}')
        if s:
            lines.append(f'SQL: {s}')
    return '\n'.join(lines)


def validate_read_only(sql):
    stripped = re.sub(r'--[^\n]*', '', sql)
    stripped = re.sub(r'/\*.*?\*/', '', stripped, flags=re.DOTALL)
    stripped = stripped.strip().lstrip('(').strip()
    first = stripped.split(None, 1)[0].upper() if stripped else ''
    return first in ('SELECT', 'WITH')


def stream_sql(question, history=None):
    for chunk in sql_chain.stream({'question': question, 'history': _history_block(history)}):
        if chunk:
            yield chunk

def _generate_sql(question, history=None):
    raw = sql_chain.invoke({'question': question, 'history': _history_block(history)}).strip()
    return _clean_sql(raw)


def _fix_sql(question, failed_sql, error):
    raw = fix_chain.invoke({
        'question': question,
        'failed_sql': failed_sql,
        'error': error,
        'schema': get_table_info(),
    }).strip()
    return _clean_sql(raw)


def _fetch_rows(sql):
    with get_engine().connect() as conn:
        result = conn.execute(text(sql))
        cols = list(result.keys())
        rows = [tuple(r) for r in result.fetchall()]
    return rows, cols


def execute_sql(sql, question, history=None):
    """Validate + run SQL with automatic repair on failure. Returns (sql, rows, cols, retries)."""
    sql = _clean_sql(sql)
    if not validate_read_only(sql):
        raise ValueError("Only SELECT queries are allowed.")
    retries = 0
    while True:
        try:
            db.run(sql)
            rows, cols = _fetch_rows(sql)
            return sql, rows, cols, retries
        except Exception as exc:
            if retries >= MAX_RETRIES - 1:
                raise
            sql = _fix_sql(question, sql, str(exc))
            if not validate_read_only(sql):
                raise ValueError("Only SELECT queries are allowed.")
            retries += 1


def run_query(question, history=None):
    sql = _generate_sql(question, history)
    return execute_sql(sql, question, history)


def run_query_df(question, history=None):
    sql, rows, cols, retries = run_query(question, history)
    return sql, pd.DataFrame(rows, columns=cols), retries


def explain_result(question, sql, result):
    result = result or []
    if not result:
        return "The query returned no rows."
    shown = result[:10]
    rendered = str(shown)
    if len(result) > 10:
        rendered += f"  (... {len(result)} rows total)"
    out = explain_chain.invoke({
        'question': question,
        'sql': sql,
        'result': rendered,
    }).strip()
    return out


def load_csv_to_table(csv_bytes, table_name):
    df = pd.read_csv(csv_bytes)
    df.to_sql(table_name, get_engine(), if_exists='replace', index=False)
    reload_database()
    return len(df)


def drop_table(table_name):
    if not re.fullmatch(r'[A-Za-z0-9_]+', table_name):
        raise ValueError(f"Invalid table name `{table_name}`.")
    if table_name in set(CSV_TABLES.values()):
        raise ValueError(f"`{table_name}` is a seeded table and cannot be deleted.")
    if table_name not in list_tables():
        raise ValueError(f"Table `{table_name}` does not exist.")
    with get_engine().begin() as conn:
        conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
    reload_database()
    return table_name

def _best_sql(question):
    """Generate SQL and try to run it; return the best SQL we have either way."""
    sql = _generate_sql(question)
    try:
        sql, _, _, _ = execute_sql(sql, question)
    except Exception:
        pass
    return sql


def evaluate_ragas():
    context = db.get_table_info()

    rubrics = {
        'score1_description': 'Response is useless/irrelevant, contains inaccurate/deceptive/misleading information, and/or contains harmful/offensive content.',
        'score2_description': 'Response is minimally relevant and may provide some vaguely useful information, but it lacks clarity and detail.',
        'score3_description': 'Response is relevant and provides some useful content, but could be more comprehensive.',
        'score4_description': 'Response is very relevant, providing clearly defined information that addresses the instruction\'s core needs.',
        'score5_description': 'Response is useful and very accurate, clearly answering the user\'s instruction in a detailed and helpful manner.',
    }

    rubrics_score = RubricsScore(name='helpfulness', rubrics=rubrics, llm=evaluator_llm)
    context_precision = ContextPrecision(llm=evaluator_llm)

    responses = []
    for q in TEST_QUESTIONS:
        responses.append(_best_sql(q))

    samples = [
        SingleTurnSample(
            user_input=TEST_QUESTIONS[i],
            retrieved_contexts=[context],
            response=responses[i],
            reference=TEST_REFERENCES[i],
        )
        for i in range(len(TEST_QUESTIONS))
    ]
    dataset = EvaluationDataset(samples)
    return evaluate(metrics=[context_precision, rubrics_score], dataset=dataset)

def _mean_or_none(df, col):
    if col not in df.columns:
        return None
    mean = pd.to_numeric(df[col], errors="coerce").mean()
    if mean is None or (isinstance(mean, float) and (math.isnan(mean) or math.isinf(mean))):
        return None
    return float(mean)


def save_eval_run(df):
    entry = {
        'timestamp': datetime.now().isoformat(timespec='seconds'),
        'context_precision': _mean_or_none(df, 'context_precision'),
        'helpfulness': _mean_or_none(df, 'helpfulness'),
    }
    history = load_eval_history()
    history.append(entry)
    with open(EVAL_HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)
    return entry

def load_eval_history():
    if not os.path.exists(EVAL_HISTORY_FILE):
        return []
    with open(EVAL_HISTORY_FILE) as f:
        return json.load(f)