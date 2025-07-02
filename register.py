from fastapi import APIRouter, Form, HTTPException, Response, Cookie, Depends
from passlib.context import CryptContext
from uuid import uuid4
import databases, os

DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)
pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

router = APIRouter(prefix="/users", tags=["users"])

# Dependency: sprawdź sesję
@router.get("/me")
async def me(session_id: str = Cookie(None)):
    if not session_id:
        raise HTTPException(401, "Nie jesteś zalogowany")
    row = await database.fetch_one(
        "SELECT u.company_name, u.website FROM sessions s "
        "JOIN users u ON s.user_id=u.id "
        "WHERE s.id=:sid AND s.expires_at>now()",
        values={"sid": session_id}
    )
    if not row:
        raise HTTPException(401, "Brak ważnej sesji")
    return {"company_name": row["company_name"], "website": row["website"]}

@router.post("/register")
async def register(
    username: str = Form(...),
    password: str = Form(...),
    company_name: str = Form(...),
    website: str = Form(...)
):
    hash_ = pwd_ctx.hash(password)
    try:
        await database.execute(
            "INSERT INTO users(username,password_hash,company_name,website) "
            "VALUES(:u,:p,:c,:w)",
            values={"u": username, "p": hash_, "c": company_name, "w": website}
        )
    except Exception:
        raise HTTPException(400, "Ta nazwa użytkownika jest już zajęta")
    return {"success": True}

@router.post("/login")
async def login(
    response: Response,
    username: str = Form(...),
    password: str = Form(...)
):
    row = await database.fetch_one(
        "SELECT id,password_hash FROM users WHERE username=:u",
        values={"u": username}
    )
    if not row or not pwd_ctx.verify(password, row["password_hash"]):
        raise HTTPException(401, "Nieprawidłowe dane logowania")
    session_id = str(uuid4())
    await database.execute(
        "INSERT INTO sessions(id,user_id,expires_at) "
        "VALUES(:sid,:uid, now()+ interval '7 days')",
        values={"sid": session_id, "uid": row["id"]}
    )
    response.set_cookie("session_id", session_id, httponly=True, max_age=7*24*3600)
    return {"success": True}

@router.post("/logout")
async def logout(
    response: Response,
    session_id: str = Cookie(None)
):
    if session_id:
        await database.execute(
            "DELETE FROM sessions WHERE id = :sid",
            values={"sid": session_id}
        )
        response.delete_cookie("session_id")
    return {"success": True}