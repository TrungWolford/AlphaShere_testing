from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
VECTOR_STORE_ID = os.getenv("VECTOR_STORE_ID")

SYSTEM_PROMPT = """You are OptiBot, the customer-support bot for OptiSigns.com.
- Tone: helpful, factual, concise.
- Only answer using the uploaded docs.
- Max 5 bullet points; else link to the doc.
- Cite up to 3 "Article URL:" lines per reply."""

response = client.responses.create(
    model="gpt-5.4-mini",
    instructions=SYSTEM_PROMPT,
    input="How do I add a YouTube video?",
    tools=[{"type": "file_search", "vector_store_ids": [VECTOR_STORE_ID]}],
)
print(response.output_text)