import os

from flask import Flask

from .routes import api
from .service import UrlShortenerService
from .storage import SQLiteUrlRepository


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config["SERVER_NAME"] = None
    app.config["DATABASE_PATH"] = os.getenv("DATABASE_PATH", "data/url_shortener.db")
    app.config["INSTANCE_NAME"] = os.getenv("INSTANCE_NAME", "local-node")

    if test_config:
        app.config.update(test_config)

    repository = SQLiteUrlRepository(database_path=app.config["DATABASE_PATH"])
    app.extensions["url_service"] = UrlShortenerService(repository=repository)
    app.register_blueprint(api)

    @app.after_request
    def attach_instance_header(response):
        response.headers["X-Backend-Node"] = app.config["INSTANCE_NAME"]
        return response

    return app
