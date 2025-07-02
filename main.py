import os
import databases
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from register import router as users_router
from form     import router as form_router

# Połączenie z bazą
DATABASE_URL = os.getenv("DATABASE_URL")
database = databases.Database(DATABASE_URL)

app = FastAPI()

# CORS – zezwalamy na żądania z frontendu
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://zawitech-frontend.onrender.com",
        "https://zawitech.pl"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

# Rejestracja / logowanie
app.include_router(users_router)

# Pozostałe formularze (register, prompt, upload-pdf, chat itd.)
app.include_router(form_router)
