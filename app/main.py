from fastapi import FastAPI

from .routes import api, web

app = FastAPI()

app.include_router(api.router, prefix="/api")
app.include_router(web.router)

@app.get("/health")
async def health():
    return {"status": "ok"}
