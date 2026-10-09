"""Voice for vishing calls (PRD P0-4): ElevenLabs speaks each caller line, then ElevenLabs Scribe
transcribes the audio, and the harness feeds that transcript to step("call", ...).

    uv run python -m harness.voice            # all vishing attacks without audio yet
    uv run python -m harness.voice --force

Writes audio/<attack_id>.mp3 (the whole call) and audio/<attack_id>.json:
    {"voice": name, "lines": [{"script": ..., "transcript": ...}, ...]}
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from harness import db  # noqa: F401  (loads .env)
from harness.scenarios import load_attacks

AUDIO_DIR = Path(__file__).resolve().parent.parent / "audio"
API = "https://api.elevenlabs.io/v1"
TTS_MODEL = "eleven_multilingual_v2"
STT_MODEL = "scribe_v1"
# Spoofed-owner calls get a calm, trustworthy voice; institutional callers a professional one.
VOICES = {"owner": ("cjVigY5qzO86Huf0OWal", "Eric"), "institution": ("EXAVITQu4vr4xnSDxMaL", "Sarah"),
          "other": ("N2lVS1w4EtoT3dr4eOWO", "Callum")}


def _headers() -> dict:
    return {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}


def pick_voice(actor: str) -> tuple[str, str]:
    a = actor.lower()
    if a in {"user", "me"} or "owner" in a:
        return VOICES["owner"]
    if any(w in a for w in ("fraud", "support", "bank", "team", "refund", "security", "billing")):
        return VOICES["institution"]
    return VOICES["other"]


def speak(text: str, voice_id: str) -> bytes:
    r = httpx.post(f"{API}/text-to-speech/{voice_id}", params={"output_format": "mp3_44100_128"},
                   headers=_headers(), json={"text": text, "model_id": TTS_MODEL}, timeout=60)
    r.raise_for_status()
    return r.content


def transcribe(audio: bytes) -> str:
    r = httpx.post(f"{API}/speech-to-text", headers=_headers(), data={"model_id": STT_MODEL},
                   files={"file": ("line.mp3", audio, "audio/mpeg")}, timeout=60)
    r.raise_for_status()
    return r.json()["text"].strip()


def make_call(attack: dict) -> str:
    calls = [s for s in attack["steps"] if s[0] == "call"]
    voice_id, voice_name = pick_voice(calls[0][1])
    clips = [speak(text, voice_id) for _, _, text in calls]
    lines = [{"script": text, "transcript": transcribe(clip)} for (_, _, text), clip in zip(calls, clips)]
    (AUDIO_DIR / f"{attack['attack_id']}.mp3").write_bytes(b"".join(clips))  # MP3 frames concatenate cleanly
    (AUDIO_DIR / f"{attack['attack_id']}.json").write_text(
        json.dumps({"voice": voice_name, "tts_model": TTS_MODEL, "stt_model": STT_MODEL, "lines": lines}, indent=2))
    return f"{attack['attack_id']}: {len(lines)} lines, voice {voice_name}"


def load_call(attack_id: str) -> dict | None:
    path = AUDIO_DIR / f"{attack_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    AUDIO_DIR.mkdir(exist_ok=True)
    todo = [a for a in load_attacks() if a["family"] == "vishing_call"
            and (args.force or not (AUDIO_DIR / f"{a['attack_id']}.mp3").exists())]
    with ThreadPoolExecutor(4) as ex:
        for line in ex.map(make_call, todo):
            print(line)


if __name__ == "__main__":
    main()
