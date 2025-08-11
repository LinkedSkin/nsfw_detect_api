from fastapi import APIRouter, UploadFile, File, HTTPException, Form, Depends
from fastapi.responses import JSONResponse

from ..utils.rate_limiter import limit_token_or_ip

from ..detector import run_inference, all_labels, naughty_labels

import base64
import io
import re
from typing import Optional
from starlette.datastructures import UploadFile as StarletteUploadFile

router = APIRouter()

# Helper: build an UploadFile from a base64 string (supports raw b64 or data URLs)
def _upload_from_b64(b64_str: str) -> StarletteUploadFile:
    # Trim whitespace/newlines
    s = b64_str.strip()
    mime = "application/octet-stream"

    # data URL? e.g., data:image/png;base64,AAAA...
    m = re.match(r"^data:([^;]+);base64,(.*)$", s, flags=re.IGNORECASE | re.DOTALL)
    if m:
        mime = m.group(1)
        s = m.group(2)

    try:
        raw = base64.b64decode(s, validate=True)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Invalid base64 data: {e}")

    bio = io.BytesIO(raw)
    bio.seek(0)
    return StarletteUploadFile(filename="upload", file=bio, content_type=mime)


@router.post("/detect", dependencies=[Depends(limit_token_or_ip)])
def detect(
    file: Optional[UploadFile] = File(None),
    file_b64: Optional[str] = Form(None),
):
    try:
        upload: Optional[UploadFile] = file
        if upload is None:
            if file_b64:
                upload = _upload_from_b64(file_b64)
            else:
                raise HTTPException(status_code=422, detail="Missing file upload or file_b64 form field")

        results = run_inference(upload)
        return JSONResponse(content=results)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/isnude", dependencies=[Depends(limit_token_or_ip)])
def isnude(
    file: Optional[UploadFile] = File(None),
    file_b64: Optional[str] = Form(None),
):
    try:
        upload: Optional[UploadFile] = file
        if upload is None:
            if file_b64:
                upload = _upload_from_b64(file_b64)
            else:
                raise HTTPException(status_code=422, detail="Missing file upload or file_b64 form field")

        results = run_inference(upload)
        for label in results:
            if label['class'] in naughty_labels:
                return JSONResponse(content={"nude": True})
        return JSONResponse(content={"nude": False})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/list_labels")
async def list_labels():
    return JSONResponse(content={"all_labels": all_labels, 'naughty_labels': naughty_labels})