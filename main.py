from fastapi import FastAPI, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import requests
from bs4 import BeautifulSoup
from uuid import uuid4
import databases
import os

DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    text = extract_text_from_website(website)[:8000]

    # 1) Sprawdź, czy firma już istnieje
    existing = await database.fetch_one(
        "SELECT id FROM clients WHERE name = :name",
        values={"name": name}
    )

    if existing:
        # 2a) Jeśli istnieje — tylko aktualizuj extracted_text + website
        await database.execute(
            """
            UPDATE clients
            SET website = :website,
                extracted_text = :text
            WHERE name = :name
            """,
            values={"name": name, "website": website, "text": text}
        )
        client_id = existing["id"]
        message = "Dane firmy zostały zaktualizowane"
    else:
        # 2b) Jeśli nie ma — wstaw nowy rekord
        client_id = str(uuid4())
        await database.execute(
            """
            INSERT INTO clients (id, name, website, extracted_text)
            VALUES (:id, :name, :website, :text)
            """,
            values={"id": client_id, "name": name, "website": website, "text": text}
        )
        message = "Dane firmy zostały zapisane"

    return {"success": True, "message": message, "client_id": client_id}
