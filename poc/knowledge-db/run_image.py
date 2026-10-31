"""Image-as-knowledge experiment.

1. Native image ingestion: what do the systems do with a PNG / an image-only PDF?
2. Sidecar pattern: image -> OCR text -> ingest with a pointer back to the image.
   Measures OCR fidelity by image quality tier (a new way to inject WRONG facts),
   then retrieval and traceability back to the source image in each system.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import sim
from adapters import HONCHO, Aim, Honcho, http, parse_claim

OUT = "/home/user/poc/img"
os.makedirs(OUT, exist_ok=True)
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
rng = random.Random(11)
TAG = str(int(time.time()))[-6:]


def render(lines, tier, seed):
    r = random.Random(seed)
    size = {"clean": 26, "lowres": 12, "noisy": 24, "rotated_noisy": 22}[tier]
    f = ImageFont.truetype(FONT, size)
    w, h = 900, 60 + 48 * len(lines)
    if tier == "lowres":
        w, h = 420, 30 + 24 * len(lines)
    im = Image.new("L", (w, h), 245)
    d = ImageDraw.Draw(im)
    d.rectangle([6, 6, w - 7, h - 7], outline=60, width=2)  # a "diagram box"
    for i, ln in enumerate(lines):
        d.text((24, 22 + i * (size + 20 if tier != "lowres" else 24)), ln, fill=20, font=f)
    if tier in ("noisy", "rotated_noisy"):
        px = im.load()
        for _ in range(int(w * h * 0.06)):
            x, y = r.randrange(w), r.randrange(h)
            px[x, y] = r.choice([0, 90, 200, 255])
        im = im.filter(ImageFilter.GaussianBlur(0.9))
    if tier == "rotated_noisy":
        im = im.rotate(r.uniform(-5, 5), expand=True, fillcolor=245)
    return im


def ocr(path):
    p = subprocess.run(["tesseract", path, "-", "--psm", "6"], capture_output=True, text=True)
    return p.stdout.strip()


def multipart(fields, fname, ctype, data):
    b = uuid.uuid4().hex
    out = b""
    for k, v in fields.items():
        out += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    out += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
            f'Content-Type: {ctype}\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
    return out, f"multipart/form-data; boundary={b}"


def post_file(url, fields, fname, ctype, data):
    body, ct = multipart(fields, fname, ctype, data)
    req = urllib.request.Request(url, data=body, method="POST", headers={"content-type": ct})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()[:300].decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode(errors="replace")


def main():
    res = {}
    # ---------- part 1: native image ingestion
    h = Honcho(f"img{TAG}")
    png = Image.new("L", (400, 120), 255)
    ImageDraw.Draw(png).text((10, 40), "The port of svc03 is 5432.", fill=0,
                             font=ImageFont.truetype(FONT, 24))
    png_path = f"{OUT}/native.png"
    png.save(png_path)
    pdf_path = f"{OUT}/scan.pdf"
    png.convert("RGB").save(pdf_path)  # image-only PDF (a scan)
    url = f"{HONCHO}/workspaces/{h.ws}/sessions/kb/messages/upload"
    s, b = post_file(url, {"peer_id": "team"}, "native.png", "image/png", open(png_path, "rb").read())
    res["honcho_upload_png"] = {"status": s, "body": b[:200]}
    s, b = post_file(url, {"peer_id": "team"}, "scan.pdf", "application/pdf", open(pdf_path, "rb").read())
    res["honcho_upload_image_only_pdf"] = {"status": s, "body": b[:200]}
    time.sleep(1)
    h.drain()
    res["honcho_conclusions_after_scan_pdf"] = h.stored_count()

    a = Aim("m3", f"img{TAG}")
    import base64
    b64 = base64.b64encode(open(png_path, "rb").read()).decode()
    try:
        a.write("images/native.md", f"# image\n![x](data:image/png;base64,{b64})")
        r = a.query("svc03")
        res["aim_inline_base64_png"] = {"written": True, "found_by_text_query": bool(r)}
    except Exception as e:  # noqa: BLE001
        res["aim_inline_base64_png"] = {"written": False, "error": str(e)[:200]}

    # ---------- part 2: sidecar OCR pipeline
    world, _, truth_at, _, _ = sim.build(7)
    facts = [(s, a_) for s in sim.SERVICES for a_ in sim.ATTRS]
    truth = truth_at[0]
    tiers = ["clean", "lowres", "noisy", "rotated_noisy"]
    per_tier = {t: {"lines": 0, "exact": 0, "parsed": 0, "wrong_parsed": 0} for t in tiers}
    imgs = []
    for i in range(40):
        tier = tiers[i % 4]
        fs = rng.sample(facts, 3)
        lines = [sim.stmt(f[0], f[1], truth[f]) for f in fs]
        im = render(lines, tier, i)
        p = f"{OUT}/img{i:02d}_{tier}.png"
        im.save(p)
        sha = hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
        text = ocr(p)
        got = [parse_claim(l) for l in text.splitlines()]
        for f, ln in zip(fs, lines):
            per_tier[tier]["lines"] += 1
            c = next((c for c in got if c and c[0] == f[0] and c[1] == f[1]), None)
            if c:
                per_tier[tier]["parsed"] += 1
                if c[2] == truth[f]:
                    per_tier[tier]["exact"] += 1
                else:
                    per_tier[tier]["wrong_parsed"] += 1
        imgs.append({"sha": sha, "tier": tier, "facts": fs, "text": text})
    res["ocr_by_tier"] = per_tier

    # ingest OCR text with image pointer into both systems, then query + trace back
    h2 = Honcho(f"imgs{TAG}")
    a2 = Aim("m3", f"imgs{TAG}")
    for im in imgs:
        sents = [l for l in im["text"].splitlines() if parse_claim(l)]
        if not sents:
            continue
        msg = " ".join(sents)
        http("POST", f"{HONCHO}/workspaces/{h2.ws}/sessions/kb/messages",
             {"messages": [{"peer_id": "team", "content": f"[img:{im['sha']}] {msg}",
                            "metadata": {"source_image": im["sha"], "contributor": f"img:{im['sha']}"}}]})
        a2.write(f"img/{im['sha']}.md", f"# image {im['sha']}\n[img:{im['sha']}] {msg}")
    h2.drain()
    ok_h = ok_a = trace_h = trace_a = n = 0
    ledger = {c["text"]: c["users"] for c in h2.ledger()}
    for im in imgs:
        for f in im["facts"]:
            n += 1
            want = sim.stmt(f[0], f[1], truth[f])
            for c in h2.query(sim.fact_key(*f), k=10):
                if c["text"].strip() == want:
                    ok_h += 1
                    if any(u == f"img:{im['sha']}" for u in ledger.get(c["text"], [])):
                        trace_h += 1
                    break
            for c in a2.query(sim.fact_key(*f), k=10):
                if want in c["text"]:
                    ok_a += 1
                    if f"img:{im['sha']}" in c["text"]:
                        trace_a += 1
                    break
    res["sidecar"] = {"facts_in_images": n, "honcho_exact_retrieved": ok_h,
                      "honcho_traceable_to_image": trace_h,
                      "aim_exact_retrieved": ok_a, "aim_traceable_to_image": trace_a}
    json.dump(res, open("/home/user/poc/image_results.json", "w"), indent=1)
    print(json.dumps(res, indent=1))


main()
