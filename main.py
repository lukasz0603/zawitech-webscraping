from fastapi import FastAPI, Form
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from uuid import uuid4
import databases
import os

DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)

app = FastAPI()

# Jeśli formularz jest na innej domenie – pozwalamy na CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # lub ["https://twoja-strona.pl"]
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def extract_text_from_website(url: str) -> str:
    try:
        response = requests.get(url, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
        texts = soup.stripped_strings
        return " ".join(texts)
    except Exception as e:
        return f"Błąd: {e}"

@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

@app.post("/register")
async def register(name: str = Form(...), website: str = Form(...)):
    client_id = str(uuid4())
    text = extract_text_from_website(website)

    await database.execute(
        query="""
        INSERT INTO clients (id, name, website, extracted_text)
        VALUES (:id, :name, :website, :text)
        """,
        values={
            "id": client_id,
            "name": name,
            "website": website,
            "text": text[:8000]
        }
    )

    return {"success": True, "message": "Dane zostały zapisane", "client_id": client_id}


@app.post("/prompt")
async def save_prompt(name: str = Form(...), prompt: str = Form(...)):
    query = """
        UPDATE clients
        SET custom_prompt = :prompt
        WHERE name = :name
    """
    await database.execute(query=query, values={"name": name, "prompt": prompt})
    return {"success": True, "message": "Prompt zapisany pomyślnie"}
