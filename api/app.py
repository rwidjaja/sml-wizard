from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from config import load_config
from routes.publish import publish_bp
from routes.session import connections_bp, session_bp
from routes.sml import sml_bp
from routes.sources import sources_bp


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "dev-only-change-me"  # TODO: load from env before any real deployment
    CORS(app, supports_credentials=True)

    app.config["SML_WIZARD_CONFIG"] = load_config()

    app.register_blueprint(session_bp, url_prefix="/api")
    app.register_blueprint(connections_bp, url_prefix="/api")
    app.register_blueprint(sources_bp, url_prefix="/api")
    app.register_blueprint(sml_bp, url_prefix="/api")
    app.register_blueprint(publish_bp, url_prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


if __name__ == "__main__":
    create_app().run(debug=True, port=5000)
