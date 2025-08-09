import traceback
import os
import shutil
import tempfile
from fastapi import UploadFile
from nudenet import NudeDetector

# Load NudeNet classifier once at startup
classifier = NudeDetector()



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
