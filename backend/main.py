from datetime import datetime
from typing import Optional
 
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException, Depends, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session


app = FastAPI()


@app.get("/")
def home():
    return {"message": "FastAPI is running!"}