import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from telegram import Update
from telegram.ext import ContextTypes

from advisor.Telegram.runner import run as run_telegram_bot  # adjust path to your module


@pytest.fixture
def env_setup(monkeypatch, tmp_path):
    """Fixture to simulate a valid .env file."""
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=dummy_token\n")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy_token")
    return env_file


@pytest.fixture
def messenger(env_setup, monkeypatch):
    """Initialize TelegramMessenger with valid token."""
    monkeypatch.setattr("pathlib.Path.resolve", lambda self: env_setup)
    m = asyncio.run(run_telegram_bot())
    m.BOT_TOKEN = "dummy_token"
    return m

@pytest.fixture(autouse=True)
def start_telegram_async(monkeypatch):
    """Automatically start the Telegram bot asynchronously for tests."""
    monkeypatch.setattr("pathlib.Path.resolve", lambda self: "/non/existent/path")  # prevent actual .env loading
    m = asyncio.run(run_telegram_bot())
    yield m
    # Teardown can be added here if needed


# ---------------------------------------------------
# Initialization tests
# ---------------------------------------------------

def test_missing_env_token(monkeypatch, tmp_path):
    """Should raise ValueError if token missing."""
    env_file = tmp_path / ".env"
    env_file.write_text("")  # no token
    monkeypatch.setattr("pathlib.Path.resolve", lambda self: env_file)

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN not found"):
        start_telegram_async(monkeypatch)


def test_env_loaded_correctly(env_setup, monkeypatch):
    """Should load .env from correct path."""
    monkeypatch.setattr("pathlib.Path.resolve", lambda self: env_setup)
    m = start_telegram_async(monkeypatch)
    assert m.BOT_TOKEN == "dummy_token"


# ---------------------------------------------------
# Command Handler tests
# ---------------------------------------------------

@pytest.mark.asyncio
async def test_start_sets_chat_id_and_sends_message(messenger):
    mock_update = MagicMock(spec=Update)
    mock_update.effective_chat.id = 999
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.bot.send_message = AsyncMock()  # ✅ FIXED

    await messenger.start(mock_update, mock_context)
    mock_context.bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_triggers_callback_and_message(messenger):
    """Check /stop triggers callback if set."""
    called = False

    def dummy_callback():
        nonlocal called
        called = True

    messenger.set_stop_callback(dummy_callback)
    mock_update = MagicMock(spec=Update)
    mock_update.effective_chat.id = 999
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.bot.send_message = AsyncMock()

    await messenger.stop(mock_update, mock_context)

    assert not messenger.should_run
    assert called
    mock_context.bot.send_message.assert_called_once()
    assert "🛑 Advisor stopped" in mock_context.bot.send_message.call_args[1]["text"]


@pytest.mark.asyncio
async def test_status_sends_account_info(messenger):
    """Check /status command sends account info."""
    mock_update = MagicMock(spec=Update)
    mock_update.effective_chat.id = 123
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.bot.send_message = AsyncMock()

    await messenger.status(mock_update, mock_context)
    mock_context.bot.send_message.assert_called_once()
    text = mock_context.bot.send_message.call_args[1]["text"]
    assert "📊 Account Status" in text


# ---------------------------------------------------
# Message sending tests
# ---------------------------------------------------

@patch("requests.post")
def test_send_message_success(mock_post, messenger):
    """Message should send successfully."""
    messenger.chat_id = 999
    mock_post.return_value.status_code = 200

    messenger.send_message("Test message")

    mock_post.assert_called_once()
    assert "sendMessage" in mock_post.call_args[0][0]


@patch("requests.post")
def test_send_message_failure(mock_post, messenger):
    """Should logger.info error when Telegram returns non-200."""
    messenger.chat_id = 999
    mock_post.return_value.status_code = 400
    mock_post.return_value.text = "Bad Request"

    messenger.send_message("Failure case")
    mock_post.assert_called_once()


def test_send_message_no_chat_id(messenger, capsys):
    """Should warn when chat_id missing."""
    messenger.chat_id = None
    messenger.send_message("Hello")
    output = capsys.readouterr().out
    assert "❌ Chat ID not set" in output


# ---------------------------------------------------
# Threaded run tests
# ---------------------------------------------------

@patch("threading.Thread")
def test_run_bot_async_starts_thread(mock_thread, messenger):
    """Ensure run_bot_async starts a daemon thread."""
    messenger.run_bot_async()
    mock_thread.assert_called_once()
    args, kwargs = mock_thread.call_args
    assert kwargs["daemon"] is True
