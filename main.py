import os
import io
import logging
from uuid import uuid4

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends, HTTPException, Form, UploadFile, File, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, HttpUrl, EmailStr
from databases import Database
from passlib.context import CryptContext
from PyPDF2 import PdfReader

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment config
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/mydb")
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")

database = Database(DATABASE_URL)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
app = FastAPI(title="Client & User Management API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency
async def get_db() -> Database:
    return database

# Pydantic Schemas
class ClientIn(BaseModel):
    name: str
    website: HttpUrl

class ClientOut(BaseModel):
    client_id: str
    message: str
    success: bool

class PromptIn(BaseModel):
    name: str
    prompt: str

class PDFUploadResponse(BaseModel):
    success: bool
    message: str

class UserRegisterIn(BaseModel):
    username: str
    password: str
    email: EmailStr

class UserLoginIn(BaseModel):
    login: str
    password: str

# Utility functions
def extract_text_from_website(url: str) -> str:
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        return " ".join(soup.stripped_strings)[:8000]
    except Exception as e:
        logger.error(f"Website extraction failed for {url}: {e}")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY,
                            detail="Failed to fetch or parse website content.")

# Lifecycle events
@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

# Routes
@app.post("/register", response_model=ClientOut)
async def register_client(payload: ClientIn, db: Database = Depends(get_db)):
    txt = extract_text_from_website(payload.website)
    existing = await db.fetch_one(
        "SELECT id FROM clients WHERE name = :name",
        values={"name": payload.name}
    )
    if existing:
        await db.execute(
            """
            UPDATE clients
            SET website = :website,
                extracted_text = :text,
                extracted_text_timestamp = NOW()
            WHERE name = :name
            """,
            values={"name": payload.name, "website": str(payload.website), "text": txt}
        )
        client_id = existing["id"]
        msg = "Client updated successfully"
    else:
        client_id = str(uuid4())
        await db.execute(
            """
            INSERT INTO clients(id, name, website, extracted_text, extracted_text_timestamp)
            VALUES(:id, :name, :website, :text, NOW())
            """,
            values={"id": client_id, "name": payload.name, "website": str(payload.website), "text": txt}
        )
        msg = "Client registered successfully"
    return {"success": True, "message": msg, "client_id": client_id}

@app.post("/prompt")
async def save_prompt(payload: PromptIn, db: Database = Depends(get_db)):
    await db.execute(
        """
        UPDATE clients
        SET custom_prompt = :prompt,
            custom_prompt_timestamp = NOW()
        WHERE name = :name
        """,
        values={"name": payload.name, "prompt": payload.prompt[:2000]}
    )
    return {"success": True, "message": "Prompt saved successfully"}

@app.get("/client/{name}")
async def get_client(name: str, db: Database = Depends(get_db)):
    row = await db.fetch_one(
        "SELECT name, website, extracted_text, custom_prompt FROM clients WHERE name = :name",
        values={"name": name}
    )
    if not row:
        raise HTTPException(status_code=404, detail="Client not found")
    return dict(row)

@app.post("/update-data")
async def update_data(name: str = Form(...), extracted_text: str = Form(...), db: Database = Depends(get_db)):
    await db.execute(
        """
        UPDATE clients
        SET extracted_text = :text,
            extracted_text_timestamp = NOW()
        WHERE name = :name
        """,
        values={"name": name, "text": extracted_text[:8000]}
    )
    return {"success": True, "message": "Data updated successfully"}

@app.post("/upload-pdf", response_model=PDFUploadResponse)
async def upload_pdf(client_name: str = Form(...), pdf_file: UploadFile = File(...), db: Database = Depends(get_db)):
    content = await pdf_file.read()
    if pdf_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF.")
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [p.extract_text() or "" for p in reader.pages]
        text = "\n\n".join(pages)[:1000000]
    except Exception as e:
        logger.error(f"PDF parsing failed: {e}")
        raise HTTPException(status_code=400, detail="Failed to process PDF.")
    await db.execute(
        """
        INSERT INTO documents(client_name, file_name, file_data, pdf_text, uploaded_at)
        VALUES(:name, :fname, :data, :text, NOW())
        """,
        values={"name": client_name, "fname": pdf_file.filename, "data": content, "text": text}
    )
    return {"success": True, "message": f"Uploaded {pdf_file.filename}"}

@app.get("/download-pdf/{client_name}")
async def download_pdf(client_name: str, db: Database = Depends(get_db)):
    row = await db.fetch_one(
        "SELECT file_name, file_data FROM documents WHERE client_name = :name ORDER BY uploaded_at DESC LIMIT 1",
        values={"name": client_name}
    )
    if not row:
        raise HTTPException(status_code=404, detail="No PDF found for this client.")
    return StreamingResponse(io.BytesIO(row["file_data"]), media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename=\"{row['file_name']}\""
    })

@app.get("/client/{name}/pdf")
async def get_pdf_text(name: str, db: Database = Depends(get_db)):
    row = await db.fetch_one(
        "SELECT pdf_text FROM documents WHERE client_name = :name ORDER BY uploaded_at DESC LIMIT 1",
        values={"name": name}
    )
    if not row:
        raise HTTPException(status_code=404, detail="No PDF text for this client.")
    return JSONResponse({"pdf_text": row["pdf_text"]})

@app.post("/update-pdf-text")
async def update_pdf_text(name: str = Form(...), pdf_text: str = Form(...), db: Database = Depends(get_db)):
    await db.execute(
        """
        UPDATE documents
        SET pdf_text = :text
        WHERE id = (
            SELECT id FROM documents WHERE client_name = :name ORDER BY uploaded_at DESC LIMIT 1
        )
        """,
        values={"name": name, "text": pdf_text[:1000000]}
    )
    return {"success": True, "message": "PDF text updated successfully"}

@app.post("/users/register")
async def register_user(payload: UserRegisterIn, db: Database = Depends(get_db)):
    hashed = pwd_context.hash(payload.password)
    try:
        row = await db.fetch_one(
            "INSERT INTO users(username, password_hash, email) VALUES(:u, :p, :e) RETURNING embed_key",
            values={"u": payload.username, "p": hashed, "e": payload.email}
        )
    except Exception as e:
        logger.error(f"User registration failed: {e}")
        raise HTTPException(status_code=400, detail="Username or email already exists.")
    embed_key = row["embed_key"]
    await db.execute(
        "INSERT INTO clients(name, embed_key) VALUES(:n, :ek) ON CONFLICT(name) DO UPDATE SET embed_key = EXCLUDED.embed_key",
        values={"n": payload.username, "ek": embed_key}
    )
    return {"success": True, "embed_key": embed_key}

@app.post("/users/login")
async def login_user(payload: UserLoginIn, db: Database = Depends(get_db)):
    row = await db.fetch_one(
        "SELECT username, password_hash FROM users WHERE username=:l OR email=:l",
        values={"l": payload.login}
    )
    if not row or not pwd_context.verify(payload.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    return {"success": True, "username": row["username"]}

@app.post("/users/generate-embed")
async def generate_embed(username: str = Form(...), db: Database = Depends(get_db)):
    row = await db.fetch_one("SELECT embed_key FROM users WHERE username = :u", values={"u": username})
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    key = row["embed_key"] or str(uuid4())
    if row["embed_key"] is None:
        await db.execute("UPDATE users SET embed_key = :ek WHERE username = :u", values={"ek": key, "u": username})
    snippet = f'<script src="https://zawitech-frontend.onrender.com/widget.js?client_id={key}" async></script>'
    return {"snippet": snippet}

@app.get("/chats")
async def list_chats(db: Database = Depends(get_db)):
    rows = await db.fetch_all(
        """
        SELECT client_id, messages,
               to_char(timestamp AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS"Z"') AS timestamp
        FROM chats
        ORDER BY timestamp DESC
        """
    )
    return [dict(r) for r in rows]
