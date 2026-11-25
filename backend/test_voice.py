import json

import requests

import settings
import voice
from voice import from_the_command, transcribe

COMMANDS = {"QUOTE", "LOG", "BOOK"}


def test_from_the_command_skips_greetings():
    assert from_the_command("Hi Jack, can you quote Mrs Jones 1250", COMMANDS) == "QUOTE Mrs Jones 1250"
    assert from_the_command("Log. Mrs Jones, leaking rad.", COMMANDS) == "LOG Mrs Jones, leaking rad."


def test_from_the_command_leaves_other_words_alone():
    assert from_the_command("Mrs Jones wants a quote", COMMANDS) == "Mrs Jones wants a quote"


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self.body = status_code, body

    def json(self):
        return self.body


def test_transcribe_uses_the_eu_service(monkeypatch):
    monkeypatch.setattr(settings, "ASSEMBLYAI_API_KEY", "test-key")
    calls = []

    def fake_post(url, headers, files, timeout):
        calls.append((url, headers, files))
        return FakeResponse(200, {"text": " Quote Mrs Jones 1250. "})

    monkeypatch.setattr(requests, "post", fake_post)

    assert transcribe(b"ogg-bytes", "audio/ogg; codecs=opus") == "Quote Mrs Jones 1250."
    url, headers, files = calls[0]
    assert url == "https://sync.eu.assemblyai.com/v1/transcribe"
    assert headers == {"Authorization": "test-key", "X-AAI-Model": "universal-3-6-pro"}
    assert files["audio"] == ("voice-note", b"ogg-bytes", "audio/ogg")
    assert json.loads(files["config"][1]) == {"language_codes": ["en"]}


def test_transcribe_gives_up_quietly(monkeypatch, capsys):
    monkeypatch.setattr(settings, "ASSEMBLYAI_API_KEY", "test-key")
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: FakeResponse(500, {"detail": "what was said"}))

    assert transcribe(b"ogg-bytes") is None
    assert "what was said" not in capsys.readouterr().out


def test_no_key_means_no_transcription(monkeypatch):
    monkeypatch.setattr(settings, "ASSEMBLYAI_API_KEY", None)

    assert voice.transcribe(b"ogg-bytes") is None
