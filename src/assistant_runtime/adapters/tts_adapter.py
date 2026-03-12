from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Protocol
from urllib import request
import wave


@dataclass(slots=True)
class SynthesizedAudio:
    audio_path: Path
    source: str
    mime_type: str = "audio/wav"
    voice_name: str | None = None


class TTSAdapter(Protocol):
    def synthesize(self, text: str, output_path: Path, *, lang: str, speed: str = "normal") -> SynthesizedAudio:
        raise NotImplementedError


def _file_suffix_for_mime_type(mime_type: str | None) -> str:
    normalized = (mime_type or "").split(";", 1)[0].strip().lower()
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if normalized in {"audio/ogg", "application/ogg"}:
        return ".ogg"
    return ".wav"


class MockTTSAdapter:
    def synthesize(self, text: str, output_path: Path, *, lang: str, speed: str = "normal") -> SynthesizedAudio:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(16000)
            handle.writeframes(b"\x00\x00" * 1600)
        return SynthesizedAudio(audio_path=output_path, source="mock_tts")


@dataclass(slots=True)
class HttpTTSAdapter:
    endpoint: str
    auth_env_var: str | None
    provider: str = "openai_compatible"
    timeout_seconds: int = 15
    api_format: str = "json_audio_base64"
    voice: str | None = None

    def build_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json" if self.api_format == "json_audio_base64" else "audio/*",
            "Content-Type": "application/json",
        }
        token = os.getenv(self.auth_env_var) if self.auth_env_var else None
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def build_payload(self, text: str, *, lang: str, speed: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "input": text,
            "text": text,
            "lang": lang,
            "language": lang,
            "speed": speed,
        }
        if self.voice:
            payload["voice"] = self.voice
        return payload

    def synthesize(self, text: str, output_path: Path, *, lang: str, speed: str = "normal") -> SynthesizedAudio:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        http_request = request.Request(
            self.endpoint,
            data=json.dumps(self.build_payload(text, lang=lang, speed=speed)).encode("utf-8"),
            headers=self.build_headers(),
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            response_body = response.read()
            response_content_type = response.headers.get("Content-Type", "application/octet-stream")

        voice_name = None
        mime_type = response_content_type.split(";", 1)[0].strip().lower() or "audio/wav"
        audio_bytes = response_body

        if self.api_format == "json_audio_base64":
            payload = json.loads(response_body.decode("utf-8"))
            encoded_audio = str(payload["audio_base64"])
            audio_bytes = base64.b64decode(encoded_audio)
            mime_type = str(payload.get("mime_type") or mime_type or "audio/wav")
            voice_name = payload.get("voice_name") or payload.get("voice")

        final_output_path = output_path.with_suffix(_file_suffix_for_mime_type(mime_type))
        final_output_path.write_bytes(audio_bytes)
        return SynthesizedAudio(
            audio_path=final_output_path,
            source="http_tts",
            mime_type=mime_type,
            voice_name=str(voice_name) if voice_name else None,
        )


@dataclass(slots=True)
class PowerShellSpeechTTSAdapter:
    executable: str = "powershell.exe"

    def synthesize(self, text: str, output_path: Path, *, lang: str, speed: str = "normal") -> SynthesizedAudio:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = (
            "Add-Type -AssemblyName System.Speech; "
            "$voice = $null; "
            "$target = $env:TTS_LANG; "
            "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$match = $synth.GetInstalledVoices() | "
            "ForEach-Object { $_.VoiceInfo } | "
            "Where-Object { $_.Culture.Name -like \"$target*\" -or $_.Culture.TwoLetterISOLanguageName -eq $target }; "
            "if ($match) { $voice = $match[0].Name; try { $synth.SelectVoice($voice) } catch {} }; "
            "$rates = @{ slow = -2; normal = 0; fast = 2 }; "
            "$rateName = $env:TTS_RATE; if ($rates.ContainsKey($rateName)) { $synth.Rate = $rates[$rateName] }; "
            "$synth.SetOutputToWaveFile($env:TTS_OUTPUT_PATH); "
            "$synth.Speak($env:TTS_TEXT); "
            "$synth.Dispose(); "
            "if ($voice) { Write-Output $voice }"
        )
        env = os.environ.copy()
        env.update(
            {
                "TTS_TEXT": text,
                "TTS_OUTPUT_PATH": str(output_path),
                "TTS_LANG": lang.split("-", 1)[0],
                "TTS_RATE": speed,
            }
        )
        completed = subprocess.run(
            [self.executable, "-NoProfile", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )
        voice_name = completed.stdout.strip() or None
        return SynthesizedAudio(audio_path=output_path, source="powershell_tts", voice_name=voice_name)


def default_powershell_executable() -> str:
    for candidate in ("powershell.exe", "pwsh.exe"):
        if shutil.which(candidate):
            return candidate
    return "powershell.exe"