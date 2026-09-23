import pytest


@pytest.fixture(autouse=True)
def no_live_telegram(monkeypatch):
    # API tests must never poll or send using a developer's real bot token.
    from app import telegram_bot
    monkeypatch.setattr(telegram_bot, 'poll', lambda stop: None)
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
