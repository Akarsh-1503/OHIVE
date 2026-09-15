"""Smoke-test the deployed LeadForge VLM endpoint end to end.

Sends local business-card images as base64 `data:` URLs and forces the reply
through the same JSON schema the backend uses, then prints latency and
throughput. This is the reference for how `backend/app/vlm.py` should call the
endpoint — same client, same content parts, same `response_format`.

    export VLM_BASE_URL=https://<workspace>--ak-project-vlm-serve.modal.run/v1
    export VLM_API_KEY=...
    python smoke_test.py                      # all images in ../../samples
    python smoke_test.py path/to/card.jpg     # or specific files
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import pathlib
import sys
import time

from openai import OpenAI

SAMPLES_DIR = pathlib.Path(__file__).resolve().parents[2] / "samples"
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"

_NULLABLE_STRING = {"type": ["string", "null"]}

LEAD_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "first_name": _NULLABLE_STRING,
        "last_name": _NULLABLE_STRING,
        "job_title": _NULLABLE_STRING,
        "company": _NULLABLE_STRING,
        "location": _NULLABLE_STRING,
        "phone": _NULLABLE_STRING,
        "email": _NULLABLE_STRING,
        "website": _NULLABLE_STRING,
        "raw_text": {"type": "string"},
    },
    "required": [
        "first_name",
        "last_name",
        "job_title",
        "company",
        "location",
        "phone",
        "email",
        "website",
        "raw_text",
    ],
    "additionalProperties": False,
}

PROMPT = (
    "Extract the contact details from this business card.\n"
    "Rules:\n"
    "- Copy phone numbers, emails and URLs exactly as printed, including spacing.\n"
    "- If the card lists several phone numbers, use the first one.\n"
    "- location is the city, region and country as printed; omit the street line.\n"
    "- raw_text is a verbatim transcription of every line of text on the card.\n"
    "- Use null for any field the card does not show. Never invent a value."
)


def to_data_url(path: pathlib.Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def extract(client: OpenAI, model: str, path: pathlib.Path) -> tuple[dict[str, object], float, int]:
    started = time.perf_counter()
    completion = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": to_data_url(path)}},
                    {"type": "text", "text": PROMPT},
                ],
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "lead", "schema": LEAD_SCHEMA, "strict": True},
        },
        max_completion_tokens=768,
        temperature=0.0,
    )
    elapsed_s = time.perf_counter() - started
    usage = completion.usage
    out_tokens = usage.completion_tokens if usage else 0
    return json.loads(completion.choices[0].message.content or "{}"), elapsed_s, out_tokens


def main() -> int:
    base_url = os.environ.get("VLM_BASE_URL")
    api_key = os.environ.get("VLM_API_KEY")
    if not base_url or not api_key:
        print("set VLM_BASE_URL (ending in /v1) and VLM_API_KEY", file=sys.stderr)
        return 2

    model = os.environ.get("VLM_MODEL", DEFAULT_MODEL)
    paths = (
        [pathlib.Path(a) for a in sys.argv[1:]]
        if len(sys.argv) > 1
        else sorted(p for p in SAMPLES_DIR.glob("*.png"))
    )
    if not paths:
        print(f"no images found in {SAMPLES_DIR}", file=sys.stderr)
        return 2

    client = OpenAI(base_url=base_url, api_key=api_key, timeout=600.0, max_retries=0)
    print(f"models available: {[m.id for m in client.models.list().data]}\n")

    results: list[tuple[str, float, int]] = []
    for path in paths:
        lead, elapsed_s, out_tokens = extract(client, model, path)
        results.append((path.name, elapsed_s, out_tokens))
        print(f"--- {path.name}  {elapsed_s * 1000:.0f} ms  {out_tokens / elapsed_s:.1f} tok/s")
        print(json.dumps(lead, indent=2, ensure_ascii=False))
        print()

    total_s = sum(r[1] for r in results)
    total_tokens = sum(r[2] for r in results)
    print(
        f"{len(results)} cards | first {results[0][1] * 1000:.0f} ms | "
        f"mean {total_s / len(results) * 1000:.0f} ms | "
        f"{total_tokens / total_s:.1f} tok/s aggregate"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
