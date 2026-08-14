import os
from dotenv import load_dotenv

load_dotenv()
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.utilities import SQLDatabase
from langchain_google_genai import ChatGoogleGenerativeAI

# 1. Setup a clean local SQLite database connection instead of MySQL
# (Replace 'sales_data.db' with your actual sqlite file name if you have one)
sqlite_uri = "sqlite:///text_to_sql.db"
db = SQLDatabase.from_uri(sqlite_uri, sample_rows_in_table_info=3)

# 2. Define the exact strict instruction prompt
template = """Based on the table schema below, write a SQL query that would answer the user's question:
Remember: Only provide me the raw SQL query. Do not include markdown blocks like ```sql.
Provide the query in a single line without line breaks.

Table Schema: {schema}
Question: {question}
SQL Query:"""

prompt = ChatPromptTemplate.from_template(template)

# 3. Initialize Gemini 2.5 Flash with tight temperature constraints
llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    api_key=os.getenv("GOOGLE_API_KEY"),
    temperature=0.0
)

# 4. Define the clean LCEL chain pipeline
sql_chain = (
    RunnablePassthrough.assign(schema=lambda _: db.get_table_info())
    | prompt 
    | llm 
    | StrOutputParser()
)

# 5. Run the query 
resp = sql_chain.invoke({"question": "What is the total 'Line Total' for Geiss Company?"})
print("Generated SQL Query:")
print(resp)