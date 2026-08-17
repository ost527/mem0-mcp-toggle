#!/usr/bin/env python3
"""Retro-apply supersede relations to a store that predates the status field.

Before the duplicate gate existed, a corrected fact was written as a NEW memory
whose prose said the old one was wrong -- and the old one kept ranking in recall.
This walks those corrections and records the relation properly, so search returns
current truth. Nothing is deleted and no vector is touched: it only writes the
sidecar's `status` / `supersedes` maps, exactly like supersede_memory would.

The pairs are NOT guessed here. Feed it a reviewed list (`--pairs`), which is how
the judgement stays with a human: build candidates with supersede_report.py, drop
the ones that are mere cross-references, then apply what is left.

    # 1. propose (writes nothing)
    .venv/bin/python server/supersede_report.py --json /tmp/report.json
    # 2. review /tmp/pairs.json  -> [{"new": "<id>", "old": ["<id>", ...],
    #                                "reason": "why"}]
    # 3. dry run, then apply
    .venv/bin/python server/migrate_supersede.py --pairs /tmp/pairs.json
    .venv/bin/python server/migrate_supersede.py --pairs /tmp/pairs.json --apply

USAGE -- stop the backend first so nothing else writes the sidecar:
    launchctl kill TERM gui/$(id -u)/com.only-my-mem0ry.server
A timestamped copy of the sidecar is written before any change.
"""
import argparse
import json
import os
import shutil
import sys

from mem0_store import (
    expand, is_backend_up, load_meta, save_meta, record_supersede, status_of,
)

STATE = expand("~/.only-my-mem0ry")
HOST = os.environ.get("MEM0_MCP_HOST", "127.0.0.1")
PORT = int(os.environ.get("MEM0_MCP_PORT", "8765"))


def load_texts(chroma_path: str, collection: str, user: str) -> dict:
    """{id: text} straight from Chroma metadata, so we can show what changes and
    refuse to touch ids that do not exist."""
    import chromadb
    col = chromadb.PersistentClient(path=chroma_path).get_collection(collection)
    res = col.get(include=["metadatas"])
    out = {}
    for mid, meta in zip(res.get("ids") or [], res.get("metadatas") or []):
        meta = meta or {}
        if user and meta.get("user_id") != user:
            continue
        out[mid] = meta.get("data", "") or ""
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", required=True,
                    help='JSON: [{"new": id, "old": [ids], "reason": str}]')
    ap.add_argument("--meta", default=os.environ.get(
        "MEM0_META_FILE", os.path.join(STATE, "memory_meta.json")))
    ap.add_argument("--chroma", default=os.environ.get(
        "MEM0_CHROMA_PATH", os.path.join(STATE, "chroma")))
    ap.add_argument("--collection", default=os.environ.get("MEM0_COLLECTION", "mem0"))
    ap.add_argument("--user", default=os.environ.get(
        "MEM0_DEFAULT_USER", "developer_workspace"))
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    a = ap.parse_args()

    if a.apply and is_backend_up(HOST, PORT):
        sys.exit(f"Backend is running on {HOST}:{PORT}. Stop it first:\n"
                 f"  launchctl kill TERM gui/$(id -u)/com.only-my-mem0ry.server")

    pairs = json.load(open(a.pairs, encoding="utf-8"))
    texts = load_texts(a.chroma, a.collection, a.user)
    meta = load_meta(a.meta)

    planned, skipped, unknown = [], [], []
    # Work on a copy so a dry run cannot leave a half-applied sidecar behind.
    preview = json.loads(json.dumps(meta))
    for p in pairs:
        new, olds = p.get("new"), list(p.get("old") or [])
        reason = (p.get("reason") or "").strip()
        if new not in texts:
            unknown.append(("new", new))
            continue
        bad = [o for o in olds if o not in texts]
        if bad:
            unknown.extend(("old", o) for o in bad)
            olds = [o for o in olds if o in texts]
        if not olds:
            continue
        already = [o for o in olds if status_of(preview.get("status", {}), o) != "active"]
        changed = record_supersede(preview, new, olds, reason)
        if changed:
            planned.append((new, changed, reason))
        skipped.extend(already)

    print(f"검토 대상 {len(pairs)}쌍 | 적용 예정 {len(planned)}쌍 "
          f"({sum(len(c) for _, c, _ in planned)}건 supersede)")
    if unknown:
        print(f"⚠️ 존재하지 않는 id {len(unknown)}건: "
              f"{', '.join(k + ':' + str(v)[:8] for k, v in unknown[:8])}")
    if skipped:
        print(f"ℹ️ 이미 supersede 상태라 건너뜀: {len(skipped)}건")
    print()
    for new, changed, reason in planned:
        print(f"[신규 {new[:8]}] {texts[new][:100]}")
        if reason:
            print(f"   근거: {reason}")
        for o in changed:
            print(f"   ⚠️→ {o[:8]}  {texts[o][:95]}")
        print()

    if not a.apply:
        print("— dry run. 적용하려면 --apply 를 붙일 것. 아무것도 쓰지 않았다.")
        return 0
    if not planned:
        print("적용할 변경이 없다.")
        return 0

    bak = a.meta + ".bak." + __import__("time").strftime("%Y%m%d-%H%M%S")
    shutil.copy2(a.meta, bak)
    save_meta(a.meta, preview)
    after = load_meta(a.meta)
    print(f"✅ 적용됨. status {len(after['status'])}건, "
          f"supersedes {len(after['supersedes'])}건.")
    print(f"   사이드카 백업: {bak}")
    print("   되돌리려면 이 백업을 되돌려 놓거나 set_status(id, 'active') 를 쓸 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
