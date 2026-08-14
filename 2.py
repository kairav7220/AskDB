import os
import re
from dotenv import load_dotenv

load_dotenv()
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
from ragas.metrics import AspectCritic, RubricsScore, ContextPrecision, Faithfulness
from ragas.dataset_schema import SingleTurnSample, EvaluationDataset

sqlite_uri = "sqlite:///text_to_sql.db"
db = SQLDatabase.from_uri(sqlite_uri, sample_rows_in_table_info=3)
context = db.get_table_info()

template = """Based on the table schema below, write a SQL query that would answer the user's question:
Remember: Only provide me the raw SQL query. Do not include markdown blocks like ```sql.
Provide the query in a single line without line breaks.

Table Schema: {schema}
Question: {question}
SQL Query:"""

prompt = ChatPromptTemplate.from_template(template)

llm = ChatGroq(
    model='llama-3.3-70b-versatile',
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0.0
)

sql_chain = (
    RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
    | prompt
    | llm
    | StrOutputParser()
)

resp = sql_chain.invoke({"question": "What was the budget of Product 12"})
print("Generated SQL Query:")
print(resp)

query = resp.strip()
if query.startswith("```"):
    query = re.sub(r"^```(?:sql)?\s*|\s*```$", "", query, flags=re.IGNORECASE | re.MULTILINE).strip()
query = " ".join(query.split())

result = db.run(query)
print("Database Result:", result)

llm = ChatGroq(
    model='llama-3.3-70b-versatile',
    api_key=os.getenv("GROQ_API_KEY"),
    temperature=0.0
)

embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
evaluator_llm = LangchainLLMWrapper(llm)
evaluator_embeddings = LangchainEmbeddingsWrapper(embeddings)

maliciousness_definition = ("Is the response intended to harm, deceive, or exploit users?")
aspect_critic = AspectCritic(
    name="maliciousness",
    definition=maliciousness_definition,
    llm=evaluator_llm,
)

helpfulness_rubrics = {
    "score1_description": "Response is useless/irrelevant, contains inaccurate/deceptive/misleading information, and/or contains harmful/offensive content.",
    "score2_description": "Response is minimally relevant and may provide some vaguely useful information, but it lacks clarity and detail.",
    "score3_description": "Response is relevant and provides some useful content, but could be more comprehensive.",
    "score4_description": "Response is very relevant, providing clearly defined information that addresses the instruction's core needs.",
    "score5_description": "Response is useful and very comprehensive with well-defined key details beyond what explicitly asked."
}

rubrics_score = RubricsScore(name="helpfulness", rubrics=helpfulness_rubrics, llm=evaluator_llm)

# ✅ FIX: instantiate context_precision (was imported but never created)
context_precision = ContextPrecision(llm=evaluator_llm)

retrieved_contexts = [context]

user_inputs = [
    "What was the budget of Product 12",
    "What are the names of all products in the products table?",
    "List all customer names from the customers table.",
    "Find the name and state of all regions in the regions table.",
    "What is the name of the customer with Customer Index = 1"
]

responses = []

for question in user_inputs:
    raw = sql_chain.invoke({"question": question}).strip()

    # ✅ FIX: LLM returns raw SQL (no markdown), so use it directly
    # Only strip markdown fences if the model accidentally adds them
    if "```" in raw:
        match = re.search(r"```(?:sql)?\s*(.*?)\s*```", raw, re.DOTALL | re.IGNORECASE)
        sql = match.group(1).strip() if match else raw
    else:
        sql = raw  # raw SQL — use as-is

    sql = " ".join(sql.split())
    responses.append(sql)

print(f"\nCollected {len(responses)} responses")  # must be 5

references = [
    "SELECT Budget FROM products WHERE ProductName = 'Product 12';",
    "SELECT ProductName FROM products;",
    "SELECT CustomerName FROM customers;",
    "SELECT RegionName, State FROM regions;",
    "SELECT CustomerName FROM customers WHERE CustomerIndex = 1;"
]

samples = []
for i in range(len(user_inputs)):
    sample = SingleTurnSample(
        user_input=user_inputs[i],
        retrieved_contexts=list(retrieved_contexts),
        response=responses[i],
        reference=references[i],
    )
    samples.append(sample)

ragas_eval_dataset = EvaluationDataset(samples)

ragas_metrics = [context_precision, rubrics_score]

result = evaluate(
    metrics=ragas_metrics,
    dataset=ragas_eval_dataset
)
print(result)