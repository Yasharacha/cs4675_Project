from flask import Flask

from .routes import api
from .service import UrlShortenerService
from .storage import InMemoryUrlRepository


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["SERVER_NAME"] = None

    repository = InMemoryUrlRepository()
    app.extensions["url_service"] = UrlShortenerService(repository=repository)
    app.register_blueprint(api)
    return app
