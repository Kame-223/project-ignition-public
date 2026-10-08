"""アプリの中で作った予定の置き場。

- 種類は `school`（学校の用事）／`personal`（個人）。当日行動（やった記録）は予定ではないので、ここには置かない（state.json の sessions）
- events.on_day がこれを混ぜて返す → 全体画面の時間軸・札・日にち移動に、授業やカレンダーの予定と同じに出る
- **Mac の中だけに置く。git にも GitHub にも入れない**（予定の題に私的なことが入る。.gitignore 済み）
- Google カレンダーへの反映は `sync` の欄（local＝まだ送っていない／synced／failed）と `gid`（Google 側の ID）で追う（bin/gcal_push.py）。
  読み戻した予定を、同じものとして重ねない（events.py が gid で除く）
"""
import json
import pathlib
import uuid
from datetime import datetime, timedelta, timezone

from tasks_io import locked_json

JST = timezone(timedelta(hours=9))
BASE = pathlib.Path(__file__).resolve().parent.parent
FILE = BASE / "data" / "my_events.json"
KINDS = ("school", "personal")
LABEL = {"school": "学校", "personal": "個人"}


def _parse(v):
    d = datetime.fromisoformat(v)
    if d.tzinfo is None:
        raise ValueError("タイムゾーンが要る")
    return d.astimezone(JST)


def all_events():
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = d.get("events") if isinstance(d, dict) else None
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else []


def add(title, start, end, kind):
    """予定を足す。返り値: 予定の id。形が不正なら ValueError。"""
    title = str(title or "").strip()
    if not title or len(title) > 80:
        raise ValueError("題は1〜80字")
    if kind not in KINDS:
        raise ValueError("種類は school か personal")
    s, e = _parse(start), _parse(end)
    if e <= s or e - s > timedelta(hours=24):
        raise ValueError("終わりは始めより後で、24時間以内")
    eid = "me-" + uuid.uuid4().hex[:8]
    rec = {"id": eid, "title": title, "start": s.isoformat(timespec="seconds"), "end": e.isoformat(timespec="seconds"),
           "kind": kind, "sync": "local", "gid": None, "created": datetime.now(JST).isoformat(timespec="seconds")}
    with locked_json(FILE, {"events": []}) as d:
        d.setdefault("events", []).append(rec)
    return eid


def get(eid):
    return next((x for x in all_events() if x.get("id") == eid), None)


def remove(eid):
    """予定を消す。返り値: 消した予定（元に戻すときに使う）。無ければ LookupError。"""
    with locked_json(FILE, {"events": []}) as d:
        items = d.setdefault("events", [])
        rec = next((x for x in items if x.get("id") == eid), None)
        if rec is None:
            raise LookupError("その予定が見つかりません")
        d["events"] = [x for x in items if x.get("id") != eid]
        if rec.get("sync") == "synced" and rec.get("gid"):  # Google にも入っているものは、消す予定として台帳に残す（gcal_push が Google 側も消す）
            d.setdefault("tombstones", []).append({"gid": rec["gid"], "id": rec["id"], "kind": rec.get("kind", "personal")})
    return rec


def restore(rec):
    """消した予定を戻す（同じ id が無いときだけ）。"""
    with locked_json(FILE, {"events": []}) as d:
        items = d.setdefault("events", [])
        if not any(x.get("id") == rec["id"] for x in items):
            items.append({**rec, "sync": "local"})  # 戻したものは、Google にもう一度送る（Google 側で消えている場合は、予定を復活させる）
        d["tombstones"] = [t for t in d.get("tombstones", []) if t.get("id") != rec["id"]]  # 戻したので、Google 側は消さない


def reconcile(mine):
    """Google 側で直された・消された、アプリの予定（送ったもの）を、アプリにも映す。Google 側が正。
    mine: [{"id","cancelled","title","start","end"}]（events.py が読んだ、このアプリが送った予定の今の姿）。返り値: 合わせた件数。
    見つからない予定（読んだ範囲の外・取得失敗かもしれない）は、消えたとは見ない。"""
    by = {m["id"]: m for m in mine if m.get("id")}
    n = 0
    with locked_json(FILE, {"events": []}) as d:
        keep = []
        for x in d.get("events", []):
            m = by.get(x.get("id"))
            if not m or x.get("sync") != "synced":
                keep.append(x)
                continue
            if m["cancelled"]:  # Google で消された → アプリからも消す（もう一度 Google に送らない）
                n += 1
                d.setdefault("external", []).append({"id": x["id"], "what": "Google 側で消された", "title": x.get("title")})
                continue
            changed = False
            for k in ("title", "start", "end"):
                if m.get(k) and m[k] != x.get(k):
                    x[k] = m[k]
                    changed = True
            if changed:
                n += 1
                d.setdefault("external", []).append({"id": x["id"], "what": "Google 側で直された", "title": x.get("title")})
            keep.append(x)
        d["events"] = keep
        d["external"] = d.get("external", [])[-50:]
    return n


def as_calendar_items():
    """events.on_day が混ぜる形（外部の予定と同じ欄）。"""
    out = []
    for x in all_events():
        try:
            _parse(x["start"]), _parse(x["end"])
        except (KeyError, ValueError, TypeError):
            continue
        out.append({"id": x["id"], "title": x.get("title", ""), "start": x["start"], "end": x["end"], "allday": False,
                    "calendar": "アプリ（" + LABEL.get(x.get("kind"), "個人") + "）", "account": "school" if x.get("kind") == "school" else "personal", "mine": True})
    return out
