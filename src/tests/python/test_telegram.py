import pytest
from unittest.mock import patch, MagicMock
from telegram import Update
from telegram.ext import ContextTypes

from advisor.Telegram.Messanger import TelegramMessenger  # adjust path to your module


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
    m = TelegramMessenger(chat_id=12345)
    m.BOT_TOKEN = "dummy_token"
    return m


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
        TelegramMessenger()


def test_env_loaded_correctly(env_setup, monkeypatch):
    """Should load .env from correct path."""
    monkeypatch.setattr("pathlib.Path.resolve", lambda self: env_setup)
    m = TelegramMessenger()
    assert m.BOT_TOKEN == "dummy_token"


# ---------------------------------------------------
# Command Handler tests
# ---------------------------------------------------

@pytest.mark.asyncio
async def test_start_sets_chat_id_and_sends_message(messenger):
    """Check /start assigns chat_id and sends confirmation."""
    mock_update = MagicMock(spec=Update)
    mock_update.effective_chat.id = 999
    mock_context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    mock_context.bot.send_message = MagicMock()

    await messenger.start(mock_update, mock_context)

    assert messenger.chat_id == 999
    mock_context.bot.send_message.assert_called_once()
    assert "✅ Advisor started" in mock_context.bot.send_message.call_args[1]["text"]


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
    mock_context.bot.send_message = MagicMock()

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
    mock_context.bot.send_message = MagicMock()

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
    """Should print error when Telegram returns non-200."""
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
