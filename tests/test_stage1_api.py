from app import create_app


def test_healthcheck():
    app = create_app()
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_create_short_url_and_redirect_updates_analytics():
    app = create_app()
    client = app.test_client()

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


def test_invalid_url_rejected():
    app = create_app()
    client = app.test_client()

    response = client.post("/api/v1/urls", json={"url": "not-a-url"})

    assert response.status_code == 400
    assert "http or https" in response.get_json()["error"]


def test_unknown_code_returns_404():
    app = create_app()
    client = app.test_client()

    response = client.get("/missing")

    assert response.status_code == 404


def test_expired_code_returns_410():
    app = create_app()
    client = app.test_client()

    create_response = client.post(
        "/api/v1/urls",
        json={"url": "https://example.com/expired", "expires_in_days": -1},
    )

    code = create_response.get_json()["code"]
    redirect_response = client.get(f"/{code}")

    assert redirect_response.status_code == 410
