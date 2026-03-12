from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.env_loader import load_local_env
from assistant_runtime.live.runtime_service import RuntimeService
from assistant_runtime.serialization import normalize_for_json


def _normalize_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _normalize_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    if isinstance(value, tuple):
        return [_normalize_json(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_json(item) for item in value)
    return value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the current runtime scaffold against text or audio input.")
    parser.add_argument("--lang", default="hu", help="Locale to load. Default: hu")
    parser.add_argument("--text", help="Process a text input directly.")
    parser.add_argument("--audio-path", type=Path, help="Path to an audio file or passthrough text file.")
    parser.add_argument("--patient-id", help="Optional patient id from the profile registry.")
    parser.add_argument("--conversation-id", default="local-demo", help="Conversation id for the run.")
    parser.add_argument("--active-condition", action="append", default=[], help="Routing condition such as cpu_overloaded or microphone_noise_high. Can be repeated.")
    parser.add_argument("--prefer-online", action="store_true", help="Prefer online routes where available.")
    parser.add_argument("--latency-context", help="Optional latency masking context from config/latency_masking.json.")
    parser.add_argument("--latency-elapsed-ms", type=int, default=0, help="Elapsed milliseconds used for latency preamble selection.")
    parser.add_argument("--latency-channel", default="chat", choices=["chat", "ssml"], help="Output format for the latency preamble.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_local_env(ROOT)
    if not args.text and not args.audio_path:
        raise SystemExit("Provide either --text or --audio-path.")

    service = RuntimeService(ROOT, args.lang)
    kwargs = {
        "conversation_id": args.conversation_id,
        "patient_id": args.patient_id,
        "active_conditions": set(args.active_condition),
        "prefer_online": args.prefer_online,
        "latency_context": args.latency_context,
        "latency_elapsed_ms": args.latency_elapsed_ms,
        "latency_channel": args.latency_channel,
    }
    if args.text:
        result = service.process_text(args.text, **kwargs)
    else:
        result = service.process_audio(args.audio_path, **kwargs)

    print(json.dumps(normalize_for_json(asdict(result)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())