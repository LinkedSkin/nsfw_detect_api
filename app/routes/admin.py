import os
import secrets
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import Boolean, Column, DateTime, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from .auth import require_admin

load_dotenv()

# --- Database (SQLite) ---
# Uses a tiny standalone DB file just for tokens. Change the URL to integrate with your main DB if desired.
DATABASE_URL = "sqlite:///./api_tokens.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# Evaluate NETDATA_MONITOR at request time
def monitor_enabled() -> bool:
    val = str(os.getenv("NETDATA_MONITOR", "0")).strip().lower()
    return val in {"1", "true", "yes", "on"}

class ApiToken(Base):
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True)
    email = Column(String, index=True, nullable=False)
    token = Column(String, unique=True, index=True, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)

router = APIRouter()


# --- Helpers ---
CSS = '<link rel="stylesheet" href="https://unpkg.com/mvp.css" />'
META = '<meta name="robots" content="noindex, nofollow">'


def _page(title: str, body: str) -> str:
    monitor_link = '<a href="/netdata">Monitoring</a>' if monitor_enabled() else ''
    return f"""<!doctype html>
<html>
  <head>
    <title>{title}</title>
    {META}
    {CSS}
  </head>
  <body>
    <main>
      <header style="display:flex;justify-content:space-between;align-items:center;gap:1rem;">
        <div>
          <h1 style="margin-bottom:0;">{title}</h1>
          <nav>
            <a href="/admin">Tokens</a>
            {monitor_link}
          </nav>
        </div>
        <form method="post" action="/auth/logout" style="margin:0;">
          <button type="submit">Logout</button>
        </form>
      </header>
      {body}
    </main>
  </body>
</html>"""


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Routes ---
@router.get("/admin", response_class=HTMLResponse)
def admin_home(db: Session = Depends(get_db), user=Depends(require_admin)):
    tokens = db.query(ApiToken).order_by(ApiToken.created_at.desc()).all()

    rows = "".join(
        f"<tr>"
        f"<td>{t.id}</td>"
        f"<td>{t.email}</td>"
        f"<td><code>{t.token}</code></td>"
        f"<td>{'active' if t.active else 'disabled'}</td>"
        f"<td>{t.created_at:%Y-%m-%d %H:%M:%S}</td>"
        f"<td>"
        f"  <form method='post' action='/admin/tokens/{t.id}/toggle' style='display:inline'>"
        f"    <button>{'Disable' if t.active else 'Enable'}</button>"
        f"  </form>"
        f"</td>"
        f"</tr>"
        for t in tokens
    )

    body = f"""
<section>
  <h2>Create new token</h2>
  <form method="post" action="/admin/tokens/new">
    <label>Email <input name="email" type="email" required></label>
    <button type="submit">Create</button>
  </form>
</section>

<section>
  <h2>Existing tokens</h2>
  <table>
    <thead>
      <tr><th>ID</th><th>Email</th><th>Token</th><th>Status</th><th>Created</th><th>Action</th></tr>
    </thead>
    <tbody>
      {rows or '<tr><td colspan="6">No tokens yet</td></tr>'}
    </tbody>
  </table>
</section>
"""+ ("""
<section>
  <h2>Monitoring</h2>
  <p>
    <a href="/netdata">Open Netdata full screen</a>
  </p>
  <div style="height:70vh;border:1px solid #ddd;border-radius:8px;overflow:hidden;">
    <iframe src="/netdata/ui/index.html" style="width:100%;height:100%;border:0;"></iframe>
  </div>
</section>
""" if monitor_enabled() else "")

    return _page("Admin: API Tokens", body)


@router.post("/admin/tokens/new", response_class=HTMLResponse)
def create_token(email: str = Form(...), db: Session = Depends(get_db), user=Depends(require_admin)):
    # Simple token generator; shown once on success
    token = "sk_" + secrets.token_urlsafe(24)
    rec = ApiToken(email=email, token=token, active=True)
    db.add(rec)
    db.commit()

    body = f"""
<p><strong>Copy your new token now:</strong></p>
<pre>{token}</pre>
<p><a href="/admin">Back to tokens</a></p>
"""
    return _page("Token Created", body)


@router.post("/admin/tokens/{token_id}/toggle", response_class=HTMLResponse)
def toggle_token(token_id: int, db: Session = Depends(get_db), user=Depends(require_admin)):
    rec: Optional[ApiToken] = db.query(ApiToken).get(token_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Not found")
    rec.active = not rec.active
    db.commit()

    status = "active" if rec.active else "disabled"
    body = f"""
<p>Token for <code>{rec.email}</code> is now <strong>{status}</strong>.</p>
<p><a href="/admin">Back to tokens</a></p>
"""
    return _page("Token Updated", body)