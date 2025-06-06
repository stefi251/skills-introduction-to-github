import os
from dotenv import load_dotenv, find_dotenv

# 1) Explicitly locate and load the .env file
env_path = find_dotenv()     # find_dotenv() looks for .env in current and parent dirs
if not env_path:
    print("❌ .env file not found.")
    exit(1)

load_dotenv(env_path)

# 2) Attempt to read the variables
openai_key = os.getenv("OPENAI_API_KEY")
pinecone_key = os.getenv("PINECONE_API_KEY")

# 3) Verify and print results
if openai_key:
    print("✅ OPENAI_API_KEY loaded:", openai_key[:6] + "…")
else:
    print("❌ OPENAI_API_KEY is not set or not found.")

if pinecone_key:
    print("✅ PINECONE_API_KEY loaded:", pinecone_key[:6] + "…")
else:
    print("❌ PINECONE_API_KEY is not set or not found.")
