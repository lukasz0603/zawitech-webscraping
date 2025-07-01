from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import requests
from bs4 import BeautifulSoup
from uuid import uuid4
import databases, os, io



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


# 1) Upload PDF
@app.post("/upload-pdf")
async def upload_pdf(
    client_name: str = Form(...),
    pdf_file: UploadFile = File(...)
):
    data = await pdf_file.read()
    await database.execute(
      """
      INSERT INTO documents (client_name, file_name, file_data)
      VALUES (:name, :fname, :data)
      """,
      values={
        "name": client_name,
        "fname": pdf_file.filename,
        "data": data
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
    
# Pamiętaj o eventach start/stop:
@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()
