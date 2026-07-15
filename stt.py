"""Speech-to-text for diary_bot (Groq Whisper), same stack as Hermes gateway STT."""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

import aiohttp
from aiogram import BaseMiddleware, Bot
from aiogram.types import Message, TelegramObject

import keys

logger = logging.getLogger(__name__)

GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
DEFAULT_GROQ_STT_MODEL = "whisper-large-v3-turbo"

# Same knobs as Hermes gateway STT.
STT_ENABLED = os.environ.get("STT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
STT_PROVIDER = os.environ.get("STT_PROVIDER", "groq").strip().lower()
STT_GROQ_MODEL = os.environ.get("STT_GROQ_MODEL", DEFAULT_GROQ_STT_MODEL).strip() or DEFAULT_GROQ_STT_MODEL
STT_LANGUAGE = os.environ.get("STT_LANGUAGE", "ru").strip() or None
STT_ECHO_TRANSCRIPTS = os.environ.get("STT_ECHO_TRANSCRIPTS", "1").strip().lower() not in {
    "0", "false", "no", "off",
}
STT_TIMEOUT_SEC = float(os.environ.get("STT_TIMEOUT_SEC", "45") or "45")


def get_groq_api_key() -> str:
    """Resolve Groq key: env first, then optional keys.GROQ_API_KEY."""
    env_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if env_key:
        return env_key
    return (getattr(keys, "GROQ_API_KEY", None) or "").strip()


# Groq Whisper accepts only these extensions (Telegram voice is often .oga).
_GROQ_ALLOWED_EXTS = {
    ".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".opus", ".wav", ".webm",
}
# Telegram / container aliases → Groq-safe extension + MIME.
_EXT_NORMALIZE = {
    ".oga": (".ogg", "audio/ogg"),
    ".ogg": (".ogg", "audio/ogg"),
    ".opus": (".opus", "audio/opus"),
    ".mp3": (".mp3", "audio/mpeg"),
    ".mp4": (".mp4", "audio/mp4"),
    ".m4a": (".m4a", "audio/mp4"),
    ".wav": (".wav", "audio/wav"),
    ".webm": (".webm", "audio/webm"),
    ".flac": (".flac", "audio/flac"),
    ".mpeg": (".mpeg", "audio/mpeg"),
    ".mpga": (".mpga", "audio/mpeg"),
}


def _normalize_audio_name(path_or_name: str) -> tuple[str, str]:
    """Return (filename_for_groq, content_type) with allowed extension."""
    raw = Path(path_or_name or "voice.ogg")
    ext = raw.suffix.lower() or ".ogg"
    safe_ext, content_type = _EXT_NORMALIZE.get(ext, (".ogg", "audio/ogg"))
    if safe_ext not in _GROQ_ALLOWED_EXTS:
        safe_ext, content_type = ".ogg", "audio/ogg"
    # Keep a stable basename; only the extension matters for Groq type sniffing.
    return f"voice{safe_ext}", content_type


async def download_telegram_file(bot: Bot, file_id: str) -> Path:
    """Download a Telegram voice/audio file to a temp path (caller deletes)."""
    tg_file = await bot.get_file(file_id)
    src_name = tg_file.file_path or "voice.ogg"
    filename, _ctype = _normalize_audio_name(src_name)
    suffix = Path(filename).suffix
    fd, tmp_name = tempfile.mkstemp(prefix="diary_stt_", suffix=suffix)
    os.close(fd)
    dest = Path(tmp_name)
    try:
        await bot.download_file(tg_file.file_path, destination=dest)
        return dest
    except Exception:
        dest.unlink(missing_ok=True)
        raise


async def transcribe_groq(
    file_path: Path,
    *,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    language: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None,
) -> Dict[str, Any]:
    """
    Transcribe audio via Groq Whisper API (OpenAI-compatible).

    Returns:
        {"success": bool, "transcript": str, "error": str|None, "provider": "groq"}
    """
    key = (api_key if api_key is not None else get_groq_api_key()).strip()
    if not key:
        return {
            "success": False,
            "transcript": "",
            "error": "GROQ_API_KEY not set",
            "provider": "groq",
        }

    model_name = (model or STT_GROQ_MODEL).strip() or DEFAULT_GROQ_STT_MODEL
    url = f"{GROQ_BASE_URL}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {key}"}

    form = aiohttp.FormData()
    form.add_field("model", model_name)
    form.add_field("response_format", "text")
    lang = language if language is not None else STT_LANGUAGE
    if lang:
        form.add_field("language", lang)

    data = file_path.read_bytes()
    # Telegram voice notes are often .oga — Groq rejects that extension.
    filename, content_type = _normalize_audio_name(str(file_path))
    form.add_field(
        "file",
        data,
        filename=filename,
        content_type=content_type,
    )

    owns_session = session is None
    session = session or aiohttp.ClientSession()
    try:
        timeout = aiohttp.ClientTimeout(total=STT_TIMEOUT_SEC)
        async with session.post(url, data=form, headers=headers, timeout=timeout) as resp:
            body = await resp.text()
            if resp.status >= 400:
                err = body.strip()[:500] or f"HTTP {resp.status}"
                logger.error("Groq STT failed status=%s: %s", resp.status, err)
                return {
                    "success": False,
                    "transcript": "",
                    "error": err,
                    "provider": "groq",
                }
            transcript = (body or "").strip().strip('"')
            return {
                "success": True,
                "transcript": transcript,
                "error": None,
                "provider": "groq",
            }
    except Exception as exc:
        logger.exception("Groq STT request error")
        return {
            "success": False,
            "transcript": "",
            "error": str(exc),
            "provider": "groq",
        }
    finally:
        if owns_session:
            await session.close()


async def transcribe_telegram_voice(bot: Bot, file_id: str) -> Dict[str, Any]:
    """Download Telegram media and run configured STT provider."""
    if STT_PROVIDER != "groq":
        return {
            "success": False,
            "transcript": "",
            "error": f"Unsupported STT provider: {STT_PROVIDER}",
            "provider": STT_PROVIDER,
        }

    path: Optional[Path] = None
    try:
        path = await download_telegram_file(bot, file_id)
        return await transcribe_groq(path)
    finally:
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def _extract_media_file_id(message: Message) -> Optional[str]:
    if message.voice:
        return message.voice.file_id
    if message.audio:
        return message.audio.file_id
    if message.video_note:
        return message.video_note.file_id
    return None


class VoiceToTextMiddleware(BaseMiddleware):
    """
    Convert inbound voice/audio/video_note into message.text via STT.

    After transcription, handlers see a normal text Message (model_copy).
    Mirrors Hermes gateway behaviour: auto-STT + optional transcript echo.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        if not isinstance(event, Message):
            return await handler(event, data)

        # Already textual — leave alone.
        if event.text is not None:
            return await handler(event, data)

        file_id = _extract_media_file_id(event)
        if not file_id:
            return await handler(event, data)

        if not STT_ENABLED:
            await event.answer(
                "Голосовой ввод выключен. Напишите текстом или включите STT_ENABLED=1."
            )
            return None

        if STT_PROVIDER == "groq" and not get_groq_api_key():
            logger.error("Voice message received but GROQ_API_KEY is not configured")
            await event.answer(
                "Голосовой ввод не настроен (нет GROQ_API_KEY). Напишите текстом."
            )
            return None

        status_msg = None
        try:
            status_msg = await event.answer("🎙 Распознаю голос…")
        except Exception:
            logger.debug("Could not send STT status message", exc_info=True)

        result = await transcribe_telegram_voice(event.bot, file_id)
        transcript = (result.get("transcript") or "").strip()

        if not result.get("success") or not transcript:
            err = result.get("error") or "пусто"
            logger.warning("STT failed for chat=%s: %s", event.chat.id if event.chat else "?", err)
            text = "Не удалось распознать голос. Попробуйте ещё раз или напишите текстом."
            if status_msg is not None:
                try:
                    await status_msg.edit_text(text)
                    return None
                except Exception:
                    pass
            await event.answer(text)
            return None

        if STT_ECHO_TRANSCRIPTS:
            echo = f"🎤 {transcript}"
            if status_msg is not None:
                try:
                    await status_msg.edit_text(echo)
                except Exception:
                    try:
                        await event.answer(echo)
                    except Exception:
                        pass
            else:
                try:
                    await event.answer(echo)
                except Exception:
                    pass
        elif status_msg is not None:
            try:
                await status_msg.delete()
            except Exception:
                pass

        # Frozen pydantic model — inject text for existing text handlers.
        event = event.model_copy(update={"text": transcript})
        return await handler(event, data)
