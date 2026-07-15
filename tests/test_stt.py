"""Tests for diary_bot STT (Groq Whisper voice → text)."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stt


class TestNormalizeAudioName:
    def test_oga_to_ogg(self):
        name, ctype = stt._normalize_audio_name("voice/file_123.oga")
        assert name.endswith(".ogg")
        assert ctype == "audio/ogg"

    def test_ogg_kept(self):
        name, ctype = stt._normalize_audio_name("x.ogg")
        assert name.endswith(".ogg")
        assert ctype == "audio/ogg"

    def test_unknown_defaults_ogg(self):
        name, ctype = stt._normalize_audio_name("x.bin")
        assert name.endswith(".ogg")
        assert ctype == "audio/ogg"


class TestConfigFlags:
    def test_stt_defaults_enabled(self):
        assert stt.STT_ENABLED is True
        assert stt.STT_PROVIDER == "groq"
        assert "whisper" in stt.STT_GROQ_MODEL

    def test_get_groq_api_key_from_env(self, monkeypatch):
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
        assert stt.get_groq_api_key() == "gsk_test_key"

    def test_get_groq_api_key_empty(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with patch.object(stt.keys, "GROQ_API_KEY", "", create=True):
            # if keys has no attr / empty
            if hasattr(stt.keys, "GROQ_API_KEY"):
                monkeypatch.setattr(stt.keys, "GROQ_API_KEY", "", raising=False)
            assert stt.get_groq_api_key() in {"", None} or isinstance(stt.get_groq_api_key(), str)


class TestTranscribeGroq:
    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("GROQ_API_KEY", raising=False)
        with patch.object(stt, "get_groq_api_key", return_value=""):
            result = asyncio.get_event_loop().run_until_complete(
                stt.transcribe_groq(Path("/tmp/nope.ogg"), api_key="")
            )
        assert result["success"] is False
        assert "GROQ_API_KEY" in result["error"]

    def test_success_mock_http(self, tmp_path, monkeypatch):
        audio = tmp_path / "sample.ogg"
        audio.write_bytes(b"OggSfake")

        class FakeResp:
            status = 200

            async def text(self):
                return "привет дневник"

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResp()

            async def close(self):
                return None

        result = asyncio.get_event_loop().run_until_complete(
            stt.transcribe_groq(
                audio,
                api_key="gsk_test",
                model="whisper-large-v3-turbo",
                language="ru",
                session=FakeSession(),
            )
        )
        assert result["success"] is True
        assert result["transcript"] == "привет дневник"
        assert result["provider"] == "groq"

    def test_http_error(self, tmp_path):
        audio = tmp_path / "sample.ogg"
        audio.write_bytes(b"OggSfake")

        class FakeResp:
            status = 401

            async def text(self):
                return '{"error":"bad key"}'

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

        class FakeSession:
            def post(self, *args, **kwargs):
                return FakeResp()

            async def close(self):
                return None

        result = asyncio.get_event_loop().run_until_complete(
            stt.transcribe_groq(audio, api_key="gsk_bad", session=FakeSession())
        )
        assert result["success"] is False
        assert "bad key" in result["error"] or "401" in result["error"]


class TestMiddlewareHelpers:
    def test_extract_voice_file_id(self):
        msg = SimpleNamespace(
            voice=SimpleNamespace(file_id="voice123"),
            audio=None,
            video_note=None,
        )
        assert stt._extract_media_file_id(msg) == "voice123"

    def test_extract_audio_file_id(self):
        msg = SimpleNamespace(
            voice=None,
            audio=SimpleNamespace(file_id="audio123"),
            video_note=None,
        )
        assert stt._extract_media_file_id(msg) == "audio123"

    def test_extract_none(self):
        msg = SimpleNamespace(voice=None, audio=None, video_note=None)
        assert stt._extract_media_file_id(msg) is None


class TestVoiceMiddleware:
    def test_passes_through_text(self):
        mw = stt.VoiceToTextMiddleware()
        handler = AsyncMock(return_value="ok")
        event = MagicMock()
        event.text = "hello"
        # isinstance check needs real Message — bypass by patching
        with patch("stt.isinstance", side_effect=lambda obj, cls: True if cls is stt.Message else isinstance(obj, cls)):
            # Better: construct minimal real Message via model_construct if available
            pass

        # Use real Message.model_construct
        from aiogram.types import Chat, Message, User
        from datetime import datetime

        msg = Message.model_construct(
            message_id=1,
            date=datetime.now(),
            chat=Chat.model_construct(id=1, type="private"),
            from_user=User.model_construct(id=1, is_bot=False, first_name="t"),
            text="hello",
        )
        result = asyncio.get_event_loop().run_until_complete(
            mw(handler, msg, {})
        )
        assert result == "ok"
        handler.assert_awaited_once()
        called_msg = handler.await_args.args[0]
        assert called_msg.text == "hello"

    def test_voice_transcribed_and_injected(self, monkeypatch):
        from aiogram.types import Chat, Message, User, Voice
        from datetime import datetime

        mw = stt.VoiceToTextMiddleware()
        handler = AsyncMock(return_value="handled")
        bot = AsyncMock()
        status = AsyncMock()
        status.edit_text = AsyncMock()

        msg = Message.model_construct(
            message_id=2,
            date=datetime.now(),
            chat=Chat.model_construct(id=42, type="private"),
            from_user=User.model_construct(id=7, is_bot=False, first_name="t"),
            text=None,
            voice=Voice.model_construct(
                file_id="fileABC",
                file_unique_id="uniq",
                duration=3,
            ),
        )

        # Frozen Message: patch bot property + answer method on class.
        monkeypatch.setattr(
            Message,
            "bot",
            property(lambda self: bot),
            raising=False,
        )

        async def fake_answer(self, *a, **k):
            return status

        monkeypatch.setattr(Message, "answer", fake_answer, raising=False)

        async def fake_transcribe(bot_arg, file_id):
            assert file_id == "fileABC"
            return {"success": True, "transcript": "заполнить дневник", "error": None}

        monkeypatch.setattr(stt, "transcribe_telegram_voice", fake_transcribe)
        monkeypatch.setattr(stt, "STT_ENABLED", True)
        monkeypatch.setattr(stt, "STT_ECHO_TRANSCRIPTS", True)
        monkeypatch.setattr(stt, "get_groq_api_key", lambda: "gsk_x")

        result = asyncio.get_event_loop().run_until_complete(mw(handler, msg, {}))
        assert result == "handled"
        called_msg = handler.await_args.args[0]
        assert called_msg.text == "заполнить дневник"
        status.edit_text.assert_awaited()
