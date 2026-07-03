# cleanup.py (chạy 1 lần)
from openai import OpenAI
from dotenv import load_dotenv
import os

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
client.vector_stores.delete(os.getenv("VECTOR_STORE_ID"))
print("Đã xoá vector store cũ.")