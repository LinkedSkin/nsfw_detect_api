from fastapi import FastAPI
from dotenv import load_dotenv

# Routers live under routes/
from .routes import api, auth, web, admin, netdata
from .routes.netdata import mount_monitor

load_dotenv()
app = FastAPI()

# Public web UI and API
app.include_router(web.router)
app.include_router(api.router, prefix="/api")
app.include_router(auth.router)

# Admin UI (guarded by auth dependency inside that module)
app.include_router(admin.router)

# Netdata reverse proxy (guarded by auth dependency inside that module)
app.include_router(netdata.router)

# Optional: start background Netdata -> Pushcut watcher if enabled in .env
mount_monitor(app)


@app.get("/health")
async def health():
    return {"status": "ok"}