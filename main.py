# main.py

import os
import databases
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from form import router as form_router  # zakładając, że form.py leży obok main.py

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

@app.on_event("startup")
async def startup():
    await database.connect()

@app.on_event("shutdown")
async def shutdown():
    await database.disconnect()

app.include_router(form_router)
