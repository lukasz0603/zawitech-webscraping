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
