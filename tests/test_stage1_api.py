from pathlib import Path
from uuid import uuid4

from app import create_app


TEST_DB_DIR = Path("test_data")
TEST_DB_DIR.mkdir(exist_ok=True)


def make_database_path() -> str:
    return str(TEST_DB_DIR / f"{uuid4().hex}.db")


def make_client(database_path: str | None = None):
    app = create_app({"DATABASE_PATH": database_path or make_database_path()})
    return app.test_client()


def test_healthcheck():
    client = make_client()
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert response.headers["X-Backend-Node"] == "local-node"


def test_node_info_endpoint():
    client = make_client()

    response = client.get("/api/v1/node")

    assert response.status_code == 200
    assert response.get_json()["instance_name"] == "local-node"


def test_homepage_renders_gui():
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Distributed URL Shortener" in response.data
    assert b"Create Short URL" in response.data


def test_create_short_url_and_redirect_updates_analytics():
    client = make_client()

    create_response = client.post(
        "/api/v1/urls",
        json={"url": "https://example.com/docs", "expires_in_days": 7},
    )

    assert create_response.status_code == 201
    payload = create_response.get_json()
    assert payload["code"]
    assert payload["long_url"] == "https://example.com/docs"
    assert payload["click_count"] == 0
    assert payload["expires_at"] is not None

    redirect_response = client.get(f"/{payload['code']}")
    assert redirect_response.status_code == 302
    assert redirect_response.headers["Location"] == "https://example.com/docs"

    details_response = client.get(f"/api/v1/urls/{payload['code']}")
    details_payload = details_response.get_json()
    assert details_response.status_code == 200
    assert details_payload["click_count"] == 1
    assert details_payload["last_accessed_at"] is not None


def test_list_urls_returns_all_saved_mappings():
    client = make_client()

    client.post("/api/v1/urls", json={"url": "https://example.com/one", "expires_in_days": 7})
    client.post("/api/v1/urls", json={"url": "https://example.com/two"})

    response = client.get("/api/v1/urls")
    payload = response.get_json()

    assert response.status_code == 200
    assert len(payload) == 2
    assert payload[0]["long_url"] == "https://example.com/one"
    assert payload[1]["long_url"] == "https://example.com/two"
    assert payload[0]["short_url"].endswith(f"/{payload[0]['code']}")
    assert payload[1]["short_url"].endswith(f"/{payload[1]['code']}")


def test_persists_data_across_app_restarts():
    database_path = make_database_path()

    first_app = create_app({"DATABASE_PATH": database_path})
    first_client = first_app.test_client()
    create_response = first_client.post(
        "/api/v1/urls",
        json={"url": "https://example.com/persisted", "expires_in_days": 3},
    )
    code = create_response.get_json()["code"]

    second_app = create_app({"DATABASE_PATH": database_path})
    second_client = second_app.test_client()
    details_response = second_client.get(f"/api/v1/urls/{code}")

    assert details_response.status_code == 200
    assert details_response.get_json()["long_url"] == "https://example.com/persisted"


def test_invalid_url_rejected():
    client = make_client()

    response = client.post("/api/v1/urls", json={"url": "not-a-url"})

    assert response.status_code == 400
    assert "http or https" in response.get_json()["error"]


def test_unknown_code_returns_404():
    client = make_client()

    response = client.get("/missing")

    assert response.status_code == 404


def test_expired_code_returns_410():
    client = make_client()

    create_response = client.post(
        "/api/v1/urls",
        json={"url": "https://example.com/expired", "expires_in_days": -1},
    )

    code = create_response.get_json()["code"]
    redirect_response = client.get(f"/{code}")

    assert redirect_response.status_code == 410
