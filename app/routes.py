from __future__ import annotations

from http import HTTPStatus

from flask import Blueprint, current_app, jsonify, redirect, render_template, request

from .service import (
    CodeAlreadyExistsError,
    ExpiredUrlError,
    InvalidCustomCodeError,
    InvalidUrlError,
    UnknownCodeError,
    UrlShortenerService,
)

api = Blueprint("api", __name__)


def get_service() -> UrlShortenerService:
    return current_app.extensions["url_service"]


@api.get("/")
@api.get("/ui")
def index():
    return render_template("index.html"), HTTPStatus.OK


@api.get("/health")
def healthcheck():
    return jsonify({"status": "ok"}), HTTPStatus.OK


@api.get("/api/v1/node")
def get_node_info():
    return (
        jsonify(
            {
                "instance_name": current_app.config["INSTANCE_NAME"],
                "database_path": current_app.config["DATABASE_PATH"],
            }
        ),
        HTTPStatus.OK,
    )


@api.post("/api/v1/urls")
def create_short_url():
    payload = request.get_json(silent=True) or {}
    long_url = payload.get("url", "")
    expires_in_days = payload.get("expires_in_days")
    custom_code = payload.get("custom_code")

    try:
        mapping = get_service().create_short_url(
            long_url=long_url,
            expires_in_days=expires_in_days,
            custom_code=custom_code,
        )
    except (InvalidUrlError, InvalidCustomCodeError) as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.BAD_REQUEST
    except CodeAlreadyExistsError as exc:
        return jsonify({"error": str(exc)}), HTTPStatus.CONFLICT

    response = get_service().serialize(mapping)
    response["short_url"] = request.host_url.rstrip("/") + f"/{mapping.code}"
    return jsonify(response), HTTPStatus.CREATED


@api.get("/api/v1/urls")
def list_urls():
    mappings = get_service().list_urls()
    response = []
    for mapping in mappings:
        payload = get_service().serialize(mapping)
        payload["short_url"] = request.host_url.rstrip("/") + f"/{mapping.code}"
        response.append(payload)
    return jsonify(response), HTTPStatus.OK


@api.get("/api/v1/urls/<code>")
def get_url_details(code: str):
    try:
        mapping = get_service().lookup(code)
    except UnknownCodeError:
        return jsonify({"error": "Short code not found."}), HTTPStatus.NOT_FOUND

    return jsonify(get_service().serialize(mapping)), HTTPStatus.OK


@api.get("/<code>")
def redirect_short_url(code: str):
    try:
        mapping = get_service().resolve(code)
    except UnknownCodeError:
        return jsonify({"error": "Short code not found."}), HTTPStatus.NOT_FOUND
    except ExpiredUrlError:
        return jsonify({"error": "Short code has expired."}), HTTPStatus.GONE

    return redirect(mapping.long_url, code=HTTPStatus.FOUND)
