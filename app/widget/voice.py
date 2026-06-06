"""Local, free voice I/O for the capsule — no cloud, no API keys.

- speak(text): text-to-speech via Windows SAPI SpVoice (always present on Win).
- listen(): speech-to-text via the modern Windows speech recognizer
  (Windows.Media.SpeechRecognition, single dictation utterance), falling back to
  the classic SAPI in-proc recognizer if the modern one is unavailable.

Everything runs on Windows' built-in engines, so the whole voice loop stays free
and offline-capable, in keeping with the free-models-only product.
"""
from __future__ import annotations

import threading
import time

# A single dedicated TTS thread + queue so speech serialises and a new line can
# interrupt the previous one (SVSFPurgeBeforeSpeak) on the SAME voice instance.
_tts_lock = threading.Lock()
_tts_queue: list[tuple[str, int, bool]] = []
_tts_thread: threading.Thread | None = None
_tts_stop = False


def tts_available() -> bool:
    try:
        import comtypes.client as cc
        cc.CreateObject("SAPI.SpVoice")
        return True
    except Exception:
        return False


def _tts_worker() -> None:
    import pythoncom
    pythoncom.CoInitialize()
    try:
        import comtypes.client as cc
        voice = cc.CreateObject("SAPI.SpVoice")
    except Exception as exc:
        print(f"[voice] TTS init failed: {exc}", flush=True)
        return
    global _tts_thread
    while True:
        with _tts_lock:
            if not _tts_queue:
                _tts_thread = None
                return
            text, rate, interrupt = _tts_queue.pop(0)
        try:
            voice.Rate = rate
            flags = 1  # SVSFlagsAsync
            if interrupt:
                flags |= 2  # SVSFPurgeBeforeSpeak — cut off the previous line
            voice.Speak(text, flags)
            # wait for it to finish (so the queue serialises) but stay responsive
            while True:
                with _tts_lock:
                    if _tts_stop or _tts_queue:
                        # new line queued or stop requested — purge & move on
                        try:
                            voice.Speak("", 2)
                        except Exception:
                            pass
                        break
                try:
                    if voice.WaitUntilDone(120):
                        break
                except Exception:
                    break
        except Exception as exc:
            print(f"[voice] TTS speak failed: {exc}", flush=True)


def speak(text: str, rate: int = 1, interrupt: bool = True) -> bool:
    """Queue text to be spoken aloud. Returns False if there's nothing to say."""
    text = (text or "").strip()
    if not text:
        return False
    text = text[:1200]  # don't read an essay
    global _tts_thread, _tts_stop
    with _tts_lock:
        _tts_stop = False
        if interrupt:
            _tts_queue.clear()
        _tts_queue.append((text, rate, interrupt))
        if _tts_thread is None or not _tts_thread.is_alive():
            _tts_thread = threading.Thread(target=_tts_worker, daemon=True)
            _tts_thread.start()
    return True


def stop_speaking() -> None:
    global _tts_stop
    with _tts_lock:
        _tts_queue.clear()
        _tts_stop = True


# ── Speech-to-text ───────────────────────────────────────────────────────────
def stt_available() -> bool:
    try:
        import winsdk.windows.media.speechrecognition  # noqa: F401
        return True
    except Exception:
        try:
            import comtypes.client as cc
            cc.CreateObject("SAPI.SpInProcRecognizer")
            return True
        except Exception:
            return False


def _listen_winrt(timeout: float) -> str | None:
    """One dictation utterance via the modern Windows recognizer. Returns the
    transcript, '' on no-speech, or None if the engine is unavailable."""
    import asyncio

    async def _run() -> str:
        import winsdk.windows.media.speechrecognition as sr
        recognizer = sr.SpeechRecognizer()
        # default constraints = free-form dictation
        await recognizer.compile_constraints_async()
        try:
            recognizer.timeouts.babble_timeout = _td(timeout)
            recognizer.timeouts.end_silence_timeout = _td(1.2)
        except Exception:
            pass
        result = await recognizer.recognize_async()
        try:
            # 0 = Success
            status = int(result.status)
        except Exception:
            status = 0
        return result.text if status == 0 else ""

    try:
        return asyncio.run(_run())
    except Exception as exc:
        print(f"[voice] WinRT STT failed: {exc}", flush=True)
        return None


def _td(seconds: float):
    """python float seconds -> WinRT TimeSpan (datetime.timedelta works)."""
    import datetime
    return datetime.timedelta(seconds=max(0.1, float(seconds)))


def _listen_sapi(timeout: float) -> str:
    """Fallback: classic SAPI in-proc dictation, polled for `timeout` seconds."""
    import pythoncom
    pythoncom.CoInitialize()
    text = ""
    try:
        import comtypes.client as cc
        rec = cc.CreateObject("SAPI.SpInProcRecognizer")
        ctx = rec.CreateRecoContext()
        grammar = ctx.CreateGrammar()
        grammar.DictationSetState(1)
        start = time.time()
        while time.time() - start < timeout:
            try:
                ev = ctx.WaitForNotifyEvent(200)
                if ev and getattr(ev, "Result", None):
                    text += " " + ev.Result.PhraseInfo.GetText()
            except Exception:
                pass
        grammar.DictationSetState(0)
    except Exception as exc:
        print(f"[voice] SAPI STT failed: {exc}", flush=True)
    return text.strip()


def listen(timeout: float = 8.0) -> str:
    """Capture one spoken utterance and return the transcript ('' if none).
    Blocking — call from a worker thread. Tries the modern recognizer first,
    falls back to SAPI."""
    out = _listen_winrt(timeout)
    if out is not None:
        return (out or "").strip()
    return _listen_sapi(timeout)
