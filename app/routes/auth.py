# auth.py
import os, secrets
from typing import Optional
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException, Request, Form, status
from fastapi.responses import HTMLResponse
from fastapi_login import LoginManager
from dotenv import load_dotenv
load_dotenv()

ADMIN_USER = os.getenv("ADMIN_USER", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASS", "changeme")
SECRET = os.getenv("AUTH_SECRET") or os.getenv("SESSION_SECRET") or secrets.token_urlsafe(32)

router = APIRouter(prefix="/auth")
manager = LoginManager(SECRET, token_url="/auth/login")
manager.use_cookie = True
manager.cookie_name = "auth"

@manager.user_loader()
def load_user(username: str) -> Optional[dict]:
    if username == ADMIN_USER:
        return {"username": username}
    return None


# Login HTML form
@router.get("/login_form", response_class=HTMLResponse)
async def login_form():
    return (
        """
        <!doctype html>
        <html>
          <head>
            <meta name="robots" content="noindex, nofollow">
            <link rel="stylesheet" href="https://unpkg.com/mvp.css" />
            <title>Admin Login</title>
          </head>
          <body>
            <main>
              <h1>Admin Login</h1>
              <form method="post" action="/auth/login">
                <label>Username <input name="username" required></label>
                <label>Password <input name="password" type="password" required></label>
                <button type="submit">Log in</button>
              </form>
              <a href="/">Home</a>
            </main>
          </body>
        </html>
        """
    )

@router.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    from fastapi.responses import RedirectResponse, JSONResponse
    print(username, password)
    print(ADMIN_USER, ADMIN_PASS)
    if not (username == ADMIN_USER and password == ADMIN_PASS):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    token = manager.create_access_token(data={"sub": username}, expires=timedelta(minutes=1440))
    resp = RedirectResponse(url="/admin", status_code=303)
    manager.set_cookie(resp, token)
    return resp

@router.get("/me")
async def me(user=Depends(manager)):
    return user

@router.post("/logout")
async def logout():
    from fastapi.responses import RedirectResponse
    resp = RedirectResponse(url="/auth/login_form", status_code=303)
    resp.delete_cookie(manager.cookie_name)
    return resp

async def require_admin(user=Depends(manager)):
    if not user or user.get("username") != ADMIN_USER:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return user