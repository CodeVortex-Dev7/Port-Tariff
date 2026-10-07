from fastapi.testclient import TestClient
from port_tariff.main import app

client = TestClient(app)


def test_health_check_endpoint():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_calculate_validation_error():
    # Attempt request with missing required parameters
    response = client.post("/api/v1/calculate")
    assert response.status_code == 422
