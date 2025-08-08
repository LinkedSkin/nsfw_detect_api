import os
import tempfile
import shutil
import traceback

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from nudenet import NudeDetector

app = FastAPI()

# Load NudeNet classifier once at startup
classifier = NudeDetector()

@app.get("/health")
async def health():
    return {"status": "ok"}

def run_inference(file: UploadFile):
    suffix = os.path.splitext(file.filename)[1] or ".jpg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        shutil.copyfileobj(file.file, temp_file)
        temp_path = temp_file.name
    try:
        results = classifier.detect(temp_path)
    except Exception as e:
        traceback.print_exc()
        raise e
    return results

@app.post("/detect")
async def detect(file: UploadFile = File(...)):
    try:
        results = run_inference(file)
        return JSONResponse(content=results)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/isnude")
async def isnude(file: UploadFile = File(...)):
    try:
        results = run_inference(file)
        label = list(results.values())[0].get("label", "unknown")
        return {"nude": label == "nude"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))