from fastapi.testclient import TestClient
from app.main import app, run_inference
from datasets import load_dataset
import requests
import io
from fastapi import UploadFile


client = TestClient(app)



def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

def test_isnude_endpoint():
    import io
    with open("tests/fixtures/nude_sample_1.jpg", "rb") as f:
        file_data = io.BytesIO(f.read())
    response = client.post(
        "/isnude",
        files={"file": ("test.jpg", file_data, "image/jpeg")}
    )
    assert response.status_code == 200
    result = response.json()
    assert "nude" in result
    assert isinstance(result["nude"], bool)

def test_detect_endpoint():
    import io
    with open("tests/fixtures/nude_sample_2.jpg", "rb") as f:
        file_data = io.BytesIO(f.read())
    response = client.post(
        "/detect",
        files={"file": ("test.jpg", file_data, "image/jpeg")}
    )
    assert response.status_code == 200
    result = response.json()
    assert isinstance(result, list)
    if result:
        assert "label" in result[0]

def test_run_inference_direct():
    import io
    with open("tests/fixtures/nude_sample_3.jpg", "rb") as f:
        file_data = io.BytesIO(f.read())
    upload = UploadFile(filename="test.jpg", file=file_data)
    result = run_inference(upload)
    assert isinstance(result, list)
    if result:
        assert "label" in result[0]