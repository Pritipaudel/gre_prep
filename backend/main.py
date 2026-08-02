from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from database import engine, Base
import routers

# Open database connection and create tables on startup
Base.metadata.create_all(bind=engine)


def _ensure_word_definition_quiz_column():
    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("words")]
    if "definition_quiz" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE words ADD COLUMN definition_quiz JSONB"))


_ensure_word_definition_quiz_column()

app = FastAPI(
    title="GRE Vocabulary Extraction & Study Backend",
    description="Backend API for managing books, pages, words, definitions, and idioms",
    version="1.0"
)

# CORS middleware to allow stream lit frontend interface to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include main router
app.include_router(routers.router)

@app.get("/")
def home():
    return {
        "message": "FastAPI GRE Vocab Backend is running!",
        "docs_url": "/docs",
        "redoc_url": "/redoc"
    }