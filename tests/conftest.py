import os, pytest

os.environ.setdefault("SQLITE_PATH", ":memory:")
os.environ.setdefault("LITELLM_API_KEY", "test-key")
os.environ.setdefault("LITELLM_MODEL", "test-model")
os.environ.setdefault("FEISHU_APP_ID", "")
os.environ.setdefault("FEISHU_APP_SECRET", "")


@pytest.fixture
def fresh_db():
    import scripts.sqlite_db as db
    db.DB_PATH = ":memory:"
    db.init_db()
    return db


@pytest.fixture
def client():
    import sys
    sys.path.insert(0, "web")
    from app import app
    app.config["TESTING"] = True
    return app.test_client()
