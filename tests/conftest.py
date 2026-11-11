import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
if not os.environ.get("DATABASE_URL"):
    os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.pop("ANTHROPIC_API_KEY", None)
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="session")
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c

def login(c, u):
    r = c.post("/api/login", json={"username": u, "password": "demo123"}); assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}

@pytest.fixture(scope="session")
def H(client):
    return {u: login(client, u) for u in ("riya", "manav", "sana", "nora", "nikhil")}
