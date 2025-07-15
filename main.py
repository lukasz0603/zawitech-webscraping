from fastapi import FastAPI, Form, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse,JSONResponse
import requests
from bs4 import BeautifulSoup
from uuid import uuid4
import databases
import os
import io
from passlib.context import CryptContext
import uuid
from PyPDF2 import PdfReader  # <--- IMPORTUJEMY PdfReader

DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)
# Kontekst do hash’owania haseł
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

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

    existing = await database.fetch_one(
        "SELECT id FROM clients WHERE name = :name",
        values={"name": name}
    )

    if existing:
        await database.execute(
            """
            UPDATE clients
            SET website = :website,
                extracted_text = :text,
                extracted_text_timestamp = NOW()
            WHERE name = :name
            """,
            values={"name": name, "website": website, "text": text}
        )
        client_id = existing["id"]
        message = "Dane firmy zostały zaktualizowane"
    else:
        client_id = str(uuid4())
        await database.execute(
            """
            INSERT INTO clients
              (id, name, website, extracted_text, extracted_text_timestamp)
            VALUES
              (:id, :name, :website, :text, NOW())
            """,
            values={"id": client_id, "name": name, "website": website, "text": text}
        )
        message = "Dane firmy zostały zapisane"

    return {"success": True, "message": message, "client_id": client_id}



@app.post("/prompt")
async def save_prompt(name: str = Form(...), prompt: str = Form(...)):
    await database.execute(
        """
        UPDATE clients
        SET custom_prompt = :prompt,
            custom_prompt_timestamp = NOW()
        WHERE name = :name
        """,
        values={"name": name, "prompt": prompt}
    )
    return {"success": True, "message": "Prompt zapisany pomyślnie"}

# GET /client/{name} – pobiera całe konto klienta
@app.get("/client/{name}")
async def get_client(name: str):
    row = await database.fetch_one(
        """
        SELECT name, website, extracted_text, custom_prompt
        FROM clients
        WHERE name = :name
        """,
        values={"name": name}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Firma nie znaleziona")
    return dict(row)

# POST /update-data – aktualizuje ręcznie extracted_text
@app.post("/update-data")
async def update_data(name: str = Form(...), extracted_text: str = Form(...)):
    await database.execute(
        """
        UPDATE clients
        SET extracted_text = :text,
            extracted_text_timestamp = NOW()
        WHERE name = :name
        """,
        values={"name": name, "text": extracted_text[:8000]}
    )
    return {"success": True, "message": "Dane zostały zaktualizowane"}

# 1) Upload PDF z ekstrakcją tekstu
@app.post("/upload-pdf")
async def upload_pdf(
    client_name: str = Form(...),
    pdf_file: UploadFile = File(...)
):
    data = await pdf_file.read()
    # 1. parsowanie PDF z PyPDF2
    try:
        reader = PdfReader(io.BytesIO(data))
        text_pages = [page.extract_text() or "" for page in reader.pages]
        pdf_text = "\n\n".join(text_pages)
    except Exception as e:
        raise HTTPException(400, f"Nie udało się przetworzyć PDF: {e}")

    # 2. zapis do bazy w jednej transakcji
    await database.execute(
        """
        INSERT INTO documents
          (client_name, file_name, file_data, pdf_text, uploaded_at)
        VALUES
          (:name, :fname, :data, :pdf_text, NOW())
        """,
        values={
            "name": client_name,
            "fname": pdf_file.filename,
            "data": data,
            "pdf_text": pdf_text[:1000000]  # opcjonalnie przytnij długie
        }
    )

    return {"success": True, "message": f"Załadowano {pdf_file.filename}"}

# 2) Download latest PDF
@app.get("/download-pdf/{client_name}")
async def download_pdf(client_name: str):
    row = await database.fetch_one(
      """
      SELECT file_name, file_data
      FROM documents
      WHERE client_name = :name
      ORDER BY uploaded_at DESC
      LIMIT 1
      """,
      values={"name": client_name}
    )
    if not row:
        raise HTTPException(404, "Nie znaleziono pliku dla tej firmy")
    return StreamingResponse(
      io.BytesIO(row["file_data"]),
      media_type="application/pdf",
      headers={"Content-Disposition": f"attachment; filename={row['file_name']}"}
    )
# 3) Aktualizauje recznie pdf

# GET /client/{name}/pdf
@app.get("/client/{name}/pdf")
async def get_pdf_text(name: str):
    row = await database.fetch_one(
        """
        SELECT pdf_text
        FROM documents
        WHERE client_name = :name
        ORDER BY uploaded_at DESC
        LIMIT 1
        """,
        values={"name": name}
    )
    if not row:
        raise HTTPException(404, "Brak PDF dla tej firmy")
    return JSONResponse({"pdf_text": row["pdf_text"]})

# POST /update-pdf-text
@app.post("/update-pdf-text")
async def update_pdf_text(
    name: str = Form(...),
    pdf_text: str = Form(...)
):
    # aktualizujemy tylko ostatni dokument dla danego klienta
    result = await database.execute(
        """
        UPDATE documents
        SET pdf_text = :pdf_text
        WHERE id = (
          SELECT id FROM documents
          WHERE client_name = :name
          ORDER BY uploaded_at DESC
          LIMIT 1
        )
        """,
        values={"name": name, "pdf_text": pdf_text[:1000000]}
    )
    return {"success": True, "message": "PDF zaktualizowany"}
    

@app.post("/users/register")
async def register_user(
    username: str = Form(...),
    password: str = Form(...),
    email:    str = Form(...),
):
    password_hash = pwd_ctx.hash(password)

    # 1) Wstawiamy nowego użytkownika i pobieramy embed_key
    try:
        row = await database.fetch_one(
            """
            INSERT INTO users (username, password_hash, email)
            VALUES (:u, :p, :e)
            RETURNING embed_key
            """,
            values={"u": username, "p": password_hash, "e": email}
        )
    except Exception:
        raise HTTPException(400, "Użytkownik lub email już istnieje")

    embed_key = row["embed_key"]

    # 2) Upsert w tabeli clients
    await database.execute(
        """
        INSERT INTO clients (name, embed_key)
        VALUES (:name, :ek)
        ON CONFLICT (name) DO
          UPDATE SET embed_key = EXCLUDED.embed_key
        """,
        values={"name": username, "ek": embed_key}
    )

    # 3) Upsert w tabeli documents – analogicznie do clients
    await database.execute(
        """
        INSERT INTO documents (client_name, client_id)
        VALUES (:name, :ek)
        ON CONFLICT (client_name) DO
          UPDATE SET client_id = EXCLUDED.client_id
        """,
        values={"name": username, "ek": embed_key}
    )

    return {"success": True, "embed_key": embed_key}
    
# ——— Logowanie ———
@app.post("/users/login")
async def login_user(
    login:    str = Form(...),  # tu user może podać username lub email
    password: str = Form(...),
):
    # znajdź usera po username lub email
    row = await database.fetch_one(
        "SELECT username,password_hash FROM users WHERE username=:l OR email=:l",
        values={"l": login}
    )
    if not row or not pwd_ctx.verify(password, row["password_hash"]):
        raise HTTPException(401, "Nieprawidłowe dane logowania")
    return {"success": True, "username": row["username"]}

# ——— AUTOMATYZACJA ———
@app.post("/users/generate-embed")
async def generate_embed(username: str = Form(...)):
    row = await database.fetch_one(
        "SELECT embed_key FROM users WHERE username = :u",
        values={"u": username}
    )
    if not row:
        raise HTTPException(404, "Nie znaleziono użytkownika")

    embed_key = row["embed_key"] or str(uuid.uuid4())
    # jeśli było NULL, zaktualizuj
    if row["embed_key"] is None:
        await database.execute(
            "UPDATE users SET embed_key = :ek WHERE username = :u",
            values={"ek": embed_key, "u": username}
        )

     # Wstaw do bot_generetion jeśli nie istnieje
    await database.execute(
        """
        INSERT INTO bot_generetion (client_id, generated)
        VALUES (:cid, :gen)
        ON CONFLICT (client_id) DO NOTHING
        """,
        values={"cid": embed_key, "gen": True}
    )
    

    snippet = f"""<script src="https://zawitech-frontend.onrender.com/widget.js?client_id={embed_key}" async></script>"""
    return {"snippet": snippet}


@app.get("/chats")
async def list_chats(client_id: str = Query(..., description="Embed key lub ID klienta")):
    rows = await database.fetch_all(
        """
        SELECT
          id,
          client_id,
          messages,
          to_char(timestamp AT TIME ZONE 'UTC',
                  'YYYY-MM-DD"T"HH24:MI:SS"Z"')
            AS timestamp
        FROM chats
        WHERE client_id = :client_id
        ORDER BY timestamp DESC
        """,
        values={"client_id": client_id}
    )
    return [dict(row) for row in rows]
