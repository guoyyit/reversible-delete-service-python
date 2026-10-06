from fastapi import FastAPI

from app.api import router

app = FastAPI(title="Reversible Delete Resource Service")
app.include_router(router)


@app.get("/")
def read_root():
    return {"Hello": "World"}
