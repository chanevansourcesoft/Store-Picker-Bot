import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url="https://api.tokenfactory.nebius.com/v1/",
    api_key=os.environ["NEBIUS_API_KEY"],
)

print("Nemotron models available:")
for m in client.models.list():
    if "nemotron" in m.id.lower():
        print(" -", m.id)