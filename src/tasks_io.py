"""data/tasks.json（データ層の正本）を安全に読み書きする。

書き込む者が複数いる（collect.py・classroom.py・act.py）。どれもここを通す。
- 鍵: data/.tasks.lock を排他ロックしてから読み、書き終えるまで離さない（他が書いた分を古い内容で戻さない）
- 保存: 同じフォルダに一時ファイルを書き切ってから差し替える（途中で落ちても壊れた JSON を残さない）
"""
import contextlib
import fcntl
import json
import os
import pathlib
import tempfile

BASE = pathlib.Path(__file__).resolve().parent.parent
TASKS = BASE / "data" / "tasks.json"
LOCK = BASE / "data" / ".tasks.lock"


def write_json_atomic(path, obj, mode=0o644, indent=2):
    path = pathlib.Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


SCHEMA_VERSION = 1   # data/SCHEMA.md の版。形を変えたら上げて、SCHEMA.md に書く
STATUSES = {"未着手", "完了"}
LISTS = ("needs_review", "upcoming", "standing", "dismissed", "archived")


def validate(data):
    """書く前の検査。おかしければ書かずに止める。"""
    from datetime import datetime
    errs = []
    ids = set()
    for t in data.get("tasks", []):
        for k in ("id", "title", "course", "status"):
            if not t.get(k):
                errs.append(f"課題 {t.get('id')} に {k} が無い")
        if t.get("status") and t["status"] not in STATUSES:
            errs.append(f"課題 {t.get('id')} の status が不正: {t['status']}")
        if t.get("due"):
            try:
                if datetime.fromisoformat(t["due"]).tzinfo is None:
                    errs.append(f"課題 {t['id']} の締切にタイムゾーンが無い")
            except ValueError:
                errs.append(f"課題 {t.get('id')} の締切の形が不正: {t['due']}")
        if t.get("id") in ids:
            errs.append(f"課題の id が重なっている: {t.get('id')}")
        ids.add(t.get("id"))
    for key in LISTS:
        for x in data.get(key, []):
            if not x.get("id") or x["id"] in ids:
                errs.append(f"{key} の id が無いか重なっている: {x.get('id')}")
            ids.add(x.get("id"))
    if errs:
        raise ValueError("tasks.json を書かずに止めた: " + " / ".join(errs[:5]))


@contextlib.contextmanager
def locked_tasks():
    """with locked_tasks() as data: ... と書く。抜けるときに検査して保存する。例外・検査の失敗なら保存しない。"""
    with open(LOCK, "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        data = json.loads(TASKS.read_text(encoding="utf-8"))
        yield data
        data["schema_version"] = SCHEMA_VERSION
        validate(data)
        write_json_atomic(TASKS, data)


@contextlib.contextmanager
def locked_json(path, default):
    """tasks.json 以外の倉庫のファイル（facts.json など）を、鍵をかけて読み書きする。
    ファイルが無いときだけ default から始める。壊れていたら書かずに止める（全部を上書きして失わないため）。"""
    path = pathlib.Path(path)
    with open(path.parent / f".{path.name}.lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))  # 壊れていれば例外で止まる
        else:
            data = default
        yield data
        write_json_atomic(path, data)


def append_line_once(path, record, key_fields):
    """JSONL に1行足す。同じ key_fields の行がもうあれば足さない（別の操作が同時に同じ記録を書いても二重にしない）。"""
    path = pathlib.Path(path)
    with open(path.parent / f".{path.name}.lock", "a") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        key = tuple(record.get(k) for k in key_fields)
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    if tuple(json.loads(line).get(k) for k in key_fields) == key:
                        return False
                except ValueError:
                    continue
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True


SOURCES = BASE / "data" / "sources.json"


def record_source(name, ok, empty=False, unread=0, note=""):
    """取得元ごとの状態。
    last_try（試した）／last_ok（取れた）／result（ok・empty・failed）／unread（まだ読めていない数）を残す。
    見張り（health.py）は、取れていない時間と、読めていない数を見る。"""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    with locked_json(SOURCES, {}) as d:
        s = d.setdefault(name, {})
        s["last_try"] = now
        s["result"] = "failed" if not ok else ("empty" if empty else "ok")
        if ok:
            s["last_ok"] = now
        s["unread"] = unread
        s["note"] = note
