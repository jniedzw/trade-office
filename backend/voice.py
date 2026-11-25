"""Turning a WhatsApp voice note into text, with AssemblyAI's EU service."""

import json
import re

import requests

import settings

# The EU address, so audio and transcripts stay in EU data centres.
SYNC_URL = "https://sync.eu.assemblyai.com/v1/transcribe"
MODEL = "universal-3-6-pro"

# Words people say before the command, like "Hi Jack, can you quote...".
FILLER = {"hi", "hey", "hiya", "morning", "jack", "ok", "okay", "right", "can", "could",
          "you", "please", "just", "so", "um", "er", "yeah", "mate"}


def transcribe(audio, kind="audio/ogg"):
    """Return what was said in a voice note, or None if it couldn't be heard."""
    if not settings.ASSEMBLYAI_API_KEY:
        return None
    try:
        response = requests.post(
            SYNC_URL,
            headers={"Authorization": settings.ASSEMBLYAI_API_KEY, "X-AAI-Model": MODEL},
            files={
                # WhatsApp sends "audio/ogg; codecs=opus", but the type is all AssemblyAI needs.
                "audio": ("voice-note", audio, kind.split(";")[0]),
                "config": (None, json.dumps({"language_codes": ["en"]}), "application/json"),
            },
            # Their server gives up after 30 seconds, so we wait a little longer.
            timeout=35,
        )
    except requests.RequestException as error:
        print(f"Voice note failed: {type(error).__name__}", flush=True)
        return None
    if response.status_code != 200:
        # Only the status: the error body could repeat what was said.
        print(f"Voice note failed: {response.status_code}", flush=True)
        return None
    text = response.json().get("text", "").strip()
    return text or None


def from_the_command(text, commands):
    """Return the transcript starting at the command, skipping words like "Hi Jack, can you".

    If no command comes within the first few words, return the text unchanged.
    """
    words = text.split()
    for position, word in enumerate(words[:6]):
        plain = re.sub(r"[^\w]", "", word).upper()
        if plain in commands:
            # Keep the rest as said, with the command word tidied of punctuation.
            return " ".join([plain, *words[position + 1:]])
        if plain.lower() not in FILLER:
            break
    return text
