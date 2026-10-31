import pytest


@pytest.fixture(autouse=True)
def isolate_secrets(monkeypatch):
    # Test runs must never load a developer's real .env or contact their accounts.
    monkeypatch.setattr('dotenv.load_dotenv', lambda *args, **kwargs: False)
    for name in ('SPOTIFY_CLIENT_ID', 'OPENAI_API_KEY', 'SPOTIFY_REDIRECT_URI'):
        monkeypatch.delenv(name, raising=False)
