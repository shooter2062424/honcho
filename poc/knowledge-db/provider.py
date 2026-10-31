"""POC LLM + embedding provider for Honcho.

Stands in for a *truth-agnostic but otherwise perfect* extractor, so the POC
measures the memory system's handling of claims, not LLM quality.

* Deriver prompts: every sentence of 3+ words written by the target peer becomes an
  explicit conclusion, true or false, fact or chit-chat. A real LLM cannot tell a
  wrong claim from a right one either; it extracts both.
* Embeddings: hashed bag-of-words (word + bigram), L2-normalised. Cosine similarity
  then tracks lexical overlap, which is the strongest similarity a template corpus
  can have without a real embedding model.
* Everything else falls back to Honcho's own mock provider.
* LLM_LATENCY_MS adds a fixed sleep per chat call to emulate provider latency.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import struct
import base64
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.mock_provider import chat as mock_chat
from src.mock_provider.schemas import ChatCompletionRequest

LATENCY = float(os.environ.get("LLM_LATENCY_MS", "0")) / 1000.0
DIMS = 1536

app = FastAPI()

_MSG = re.compile(
    r'<message idx="(\d+)" peer="([^"]+)" target="(true|false)"[^>]*>(.*?)</message>',
    re.S,
)
_TAG = re.compile(r"^\s*\[[^\]]*\]\s*")
_SENT = re.compile(r"(?<=[.!?])\s+")
STATS = {"chat": 0, "derive": 0, "embed_inputs": 0}


def _derive(prompt: str) -> str:
    out: list[dict[str, Any]] = []
    for idx, _peer, target, body in _MSG.findall(prompt):
        if target != "true":
            continue
        body = _TAG.sub("", body.replace("&lt;", "<"))
        for sent in _SENT.split(body.strip()):
            sent = sent.strip()
            if len(sent.split()) >= 3:
                out.append({"source_indices": [int(idx)], "content": sent})
    return json.dumps({"explicit": out})


def _vec(text: str) -> list[float]:
    words = re.findall(r"[a-z0-9_.\-]+", text.lower())
    feats = words + [f"{a}_{b}" for a, b in zip(words, words[1:])]
    v = [0.0] * DIMS
    for f in feats:
        h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "big")
        v[h % DIMS] += 1.0 if (h >> 63) & 1 else -1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@app.post("/v1/embeddings")
async def embeddings(body: dict[str, Any]) -> Any:
    raw = body.get("input")
    texts = [raw] if isinstance(raw, str) else [str(x) for x in (raw or [])]
    STATS["embed_inputs"] += len(texts)
    dims = int(body.get("dimensions") or DIMS)
    data = []
    for i, t in enumerate(texts):
        vec = _vec(t)[:dims]
        b64 = base64.b64encode(struct.pack(f"<{len(vec)}f", *vec)).decode()
        data.append(
            {
                "object": "embedding",
                "index": i,
                "embedding": b64 if body.get("encoding_format") == "base64" else vec,
            }
        )
    return {
        "object": "list",
        "data": data,
        "model": body.get("model", "poc-bow"),
        "usage": {"prompt_tokens": 1, "total_tokens": 1},
    }


@app.post("/v1/chat/completions")
async def chat(body: ChatCompletionRequest) -> Any:
    STATS["chat"] += 1
    if LATENCY:
        await asyncio.sleep(LATENCY)
    text = "\n".join(str(m.content) for m in body.messages if m.content)
    if "extract **explicit atomic facts**" in text or "<messages>" in text:
        STATS["derive"] += 1
        content = _derive(text)
        return {
            "id": "chatcmpl-poc",
            "object": "chat.completion",
            "created": 1577836800,
            "model": body.model or "poc",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": max(1, len(text) // 4),
                "completion_tokens": max(1, len(content) // 4),
                "total_tokens": max(2, (len(text) + len(content)) // 4),
            },
        }
    return await mock_chat.chat_completions(body)


@app.get("/stats")
async def stats() -> Any:
    return JSONResponse(STATS)


@app.get("/health")
async def health() -> Any:
    return {"ok": True}
