from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

CSS_LINK = '<link rel="stylesheet" href="https://unpkg.com/mvp.css" />'
META_ROBOTS = '<meta name="robots" content="noindex, nofollow">'

@router.get("/", response_class=HTMLResponse)
async def home():
    return f"""
    <html>
      <head>
        <title>NSFW Detect</title>
        {META_ROBOTS}
        {CSS_LINK}
      </head>
      <body>
        <main>
          <h1>NSFW Detect</h1>
          <nav>
            <a href="/detect_form">Detect (full results)</a>
            <a href="/isnude_form">Is Nude? (boolean)</a>
            <a href="/api/list_labels">Label List</a>
          </nav>
        </main>
      </body>
    </html>
    """

@router.get("/detect_form", response_class=HTMLResponse)
async def detect_form():
    return f"""
    <html>
      <head>
        <title>Detect</title>
        {META_ROBOTS}
        {CSS_LINK}
      </head>
      <body>
        <main>
          <h2>Detect (full results)</h2>
          <form method="post" action="/api/detect" enctype="multipart/form-data">
            <input type="file" name="file" accept="image/*" required />
            <button type="submit">Upload</button>
          </form>
          <p><a href="/">Back</a></p>
        </main>
      </body>
    </html>
    """

@router.get("/isnude_form", response_class=HTMLResponse)
async def isnude_form():
    return f"""
    <html>
      <head>
        <title>Is Nude?</title>
        {META_ROBOTS}
        {CSS_LINK}
      </head>
      <body>
        <main>
          <h2>Is Nude?</h2>
          <form method="post" action="/api/isnude" enctype="multipart/form-data">
            <input type="file" name="file" accept="image/*" required />
            <button type="submit">Check</button>
          </form>
          <p><a href="/">Back</a></p>
        </main>
      </body>
    </html>
    """