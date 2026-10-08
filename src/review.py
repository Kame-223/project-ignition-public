#!/usr/local/bin/python3
"""要確認・先の予定・いつもの決まりを、ユーザーの判断で片付ける。

倉庫の見直しで追加。ユーザーが「これは違う」「要らない」と言ったものは tasks.json の dismissed に残し、
classroom.py が投稿を読み直しても同じもの（同じ科目・同じ題）をまた出さないようにする。
消すのではなく残す（なぜ要らないと判断したかも書く）。

要確認を1件ずつボタンで選ぶページ（bin/review_page.py）から呼べるように、判断を関数に分けた。
ユーザーの判断は全部 tasks.json の decisions に足す）。
置き場所の変更と同じ鍵・同じ保存で書くので、記録と中身がずれない。

使い方:
  bin/review.py list
  bin/review.py dismiss <id> "理由"      … 要らない（また出さない）
  bin/review.py promote <id> <締切ISO>   … 課題一覧に入れる
"""
import json
import pathlib
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tasks_io import BASE, locked_tasks  # noqa: E402

JST = timezone(timedelta(hours=9))
LISTS = ("needs_review", "upcoming", "standing")
# ページのボタン → どこへ置くか
CHOICES = {"課題にする": "tasks", "もう済んだ": "dismissed", "要らない": "dismissed",
           "先の予定": "upcoming", "いつもの決まり": "standing"}


def norm(s):
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", s or "")).strip().lower()


def _grams(s):
    t = re.sub(r"[\s「」『』()、。・，,.:：;；\-ー]+", "", unicodedata.normalize("NFKC", s or "")).lower()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _nums(s):
    return sorted(re.findall(r"\d+", unicodedata.normalize("NFKC", s or "")))


def similar(a, b):
    """同じ内容の言い換えか（。同じ内容を別の言い方で書いたものなど）。
    2文字の組の重なり: 全体の重なり 0.3 以上、かつ短い方の半分以上が重なる。別の内容（0.1〜0.2）とは分かれる。"""
    if _nums(a) != _nums(b):  # 番号・日付・章が違えば別の課題（「Journal #3」と「#4」。レビュー）
        return False
    A, B = _grams(a), _grams(b)
    if len(A) < 4 or len(B) < 4:
        return norm(a) == norm(b) and bool(norm(a))
    i = len(A & B)
    return i / len(A | B) >= 0.3 and i / min(len(A), len(B)) >= 0.5


def consolidate(data, via="auto"):
    """要確認の中の、すでに答えたものと同じ内容・要確認どうしの重複を片付ける。返り値: 片付けた件数。鍵の中で呼ぶ。
    - 答え済み（要らない・もう済んだ＝dismissed／先の予定／いつもの決まり）と同じ科目で似ている → 同じ扱いにする
    - 要確認どうしで似ている → 先の1件だけ残す（残した方に答えれば、似たものも同じ扱いになる）"""
    now = datetime.now(JST).isoformat(timespec="seconds")
    n = 0
    decided = [("dismissed", x) for x in data.get("dismissed", []) if x.get("why", "").startswith("ユーザー")] + \
              [("upcoming", x) for x in data.get("upcoming", []) if x.get("decided_via")] + \
              [("standing", x) for x in data.get("standing", []) if x.get("decided_via")]
    for r in list(data.get("needs_review", [])):
        course, title = r.get("course"), r.get("title", "")
        hit = next(((k, x) for k, x in decided if x.get("course") == course and similar(x.get("title", ""), title)), None)
        if hit:
            k, x = hit
            note = f"同じ内容を答え済み（「{x.get('title', '')[:30]}」）"
            if k == "dismissed":
                dismiss(data, r["id"], f"ユーザー: {note}", now, via, ("needs_review",))
            else:
                move(data, r["id"], k, now, via, ("needs_review",))
            record(data, {"at": now, "id": r["id"], "choice": "自動: " + k, "note": note, "course": course, "title": title})
            n += 1
            continue
        # 課題一覧に似た課題があっても、自動では片付けない（締切の見落としが一番重い。レビュー）。要確認としてユーザーに見せる
    seen = []
    for r in list(data.get("needs_review", [])):
        first = next((k for k in seen if k.get("course") == r.get("course") and similar(k.get("title", ""), r.get("title", ""))), None)
        if first:
            dismiss(data, r["id"], f"同じ質問の重複（残した「{first.get('title', '')[:30]}」に答えれば同じ扱い）", now, via, ("needs_review",))
            record(data, {"at": now, "id": r["id"], "choice": "自動: 重複", "note": first.get("title", ""), "course": r.get("course"), "title": r.get("title")})
            n += 1
        else:
            seen.append(r)
    return n


def source_key(item_id):
    """どこから来たものか。投稿なら post-<科目>-<投稿>-、課題なら cw:<科目>/<課題>。"""
    item_id = str(item_id or "")
    m = re.match(r"(post-\d+-\d+-|(?:hn|sy|ml)-[0-9a-f]{8}-[0-9a-f]{4}-)", item_id)
    if m:
        return m.group(1)
    m = re.match(r"cw-(\d+/\d+)$", item_id)
    return f"cw:{m.group(1)}" if m else None


def is_dismissed(data, course, title, key=None):
    """ユーザーが要らないと言ったものか。
    - 課題（cw:）: 同じ課題なら題が変わっても却下のまま
    - 投稿（post-）: 同じ投稿から来た同じ題は却下のまま。言い換えは classroom.py が AI に却下の一覧を見せて防ぐ。
      別の投稿の同じ題は却下しない
    - 出所の分からない古い記録だけ、同じ科目・同じ題で判定する"""
    for x in data.get("dismissed", []):
        xkey = x.get("source_key")
        if xkey and key:
            if xkey.startswith("cw:") and xkey == key:
                return True
            # 同じ投稿で同じ題。言い換えかどうかは、読み直すときに AI が却下の一覧を見て判断する
            # （似ている度合いで機械的に決めると、別の章を消したり、言い換えを通したりした）
            if xkey == key and norm(x.get("title")) == norm(title):
                return True
        elif not xkey and x.get("course") == course and norm(x.get("title")) == norm(title):
            return True
    return False


def _take(data, item_id, lists=LISTS):
    for key in lists:
        for x in data.get(key, []):
            if x.get("id") == item_id:
                data[key].remove(x)
                return key, x
    raise LookupError(f"見つからない: {item_id}")


def _mark(x, via):
    """ページで決めたものに、元の姿（戻すとき用）と印を付ける。"""
    return {"decided_via": via, "before_decision": dict(x)} if via else {}


def dismiss(data, item_id, why, now, via=None, lists=LISTS):
    key, x = _take(data, item_id, lists)
    data.setdefault("dismissed", []).append({**x, "from": key, "dismissed_at": now,
                                             "source_key": source_key(x.get("id")),
                                             "why": why or "ユーザーが要らないと言った", **_mark(x, via)})
    return key, x


def promote(data, item_id, due, now, via=None, lists=LISTS):
    if due.tzinfo is None:
        raise ValueError("締切にタイムゾーン（+09:00）を付けて")
    for key in lists:  # 先に重なりを調べてから取り出す（途中で止まっても元の場所に残る）
        for x in data.get(key, []):
            if x.get("id") == item_id and any(
                    t["course"] == x.get("course") and norm(t["title"]) == norm(x.get("title"))
                    and t.get("status") != "完了" for t in data["tasks"]):
                raise ValueError(f"同じ課題がもう課題一覧にある: {x.get('title')}")
    key, x = _take(data, item_id, lists)
    new_id = x.get("id")
    if any(t["id"] == new_id for t in data["tasks"]):
        new_id = f"{new_id}-p{len(data['tasks'])}"  # 同じ id が課題一覧にあれば付け直す
    origin = x.get("origin") or ("classroom_api" if str(x.get("id", "")).startswith("cw-") else "classroom_ai")
    t = {**{k: x.get(k) for k in ("title", "course", "link", "raw", "quote", "source")}, "id": new_id,
         "due": due.isoformat(), "status": "未着手", "origin": origin,
         "certainty": "確認済み", "confirmed": f"{now[:10]} ユーザーがやると言った",
         "next_action": "何をするのかだけ確認する。", "next_action_confidence": "推定",
         "note": x.get("why", ""), "from": key, "promoted_from": x.get("id")}
    m = re.match(r"cw-(\d+/\d+)$", str(x.get("id")))
    if m:
        t["classroom_id"] = m.group(1)  # classroom.py が同じ課題を見つけられるように
    t.update(_mark(x, via))
    data["tasks"].append(t)
    return key, x


def move(data, item_id, to, now, via=None, lists=LISTS):
    """要確認 → 先の予定／いつもの決まり。"""
    if to not in ("upcoming", "standing"):
        raise ValueError(f"置き場所が不正: {to}")
    key, x = _take(data, item_id, lists)
    data.setdefault(to, []).append({**x, "from": key, "moved_at": now, "why": "ユーザーが置いた（要確認のページ）",
                                    **_mark(x, via)})
    return key, x


def undo(data, item_id, via):
    """ページで決めたものを、決める前の姿のまま要確認へ戻す。ページで決めたもの（decided_via が合うもの）だけ。"""
    for key in ("tasks", "dismissed", "upcoming", "standing", "archived"):  # 収集が古いものをしまったあとでも戻せる
        for x in data.get(key, []):
            if x.get("decided_via") == via and item_id in (x.get("id"), x.get("promoted_from")):
                if key == "tasks" and x.get("status") == "完了":
                    raise ValueError("もう完了にしたものは戻さない")
                back = x.get("before_decision")
                if not back:
                    raise ValueError("決める前の姿が残っていないので戻せない")
                if any(y.get("id") == back.get("id") for k in ("tasks",) + LISTS + ("dismissed", "archived")
                       for y in data.get(k, []) if y is not x):
                    raise ValueError(f"同じ id がもうあるので戻せない: {back.get('id')}")
                data[key].remove(x)
                data.setdefault("needs_review", []).append(dict(back))
                return key
    raise LookupError(f"ページで決めたものが見つからない: {item_id}")


def decide(data, item_id, choice, note="", due=None, via="review_page"):
    """ページのボタン1回分。置き場所を変え、見本として decisions に残す（鍵の中で呼ぶ）。"""
    if not isinstance(choice, str) or choice not in CHOICES:
        raise ValueError(f"選択が不正: {choice!r}")
    if not isinstance(note, str):
        raise ValueError("ひとことは文字で")
    now = datetime.now(JST).isoformat(timespec="seconds")
    only = ("needs_review",)  # ページが出すのは要確認だけ。二重に押しても2回目は「見つからない」になる
    item = next((x for x in data.get("needs_review", []) if x.get("id") == item_id), None)
    if item is None:
        raise LookupError(f"もう要確認にない: {item_id}")
    shown = item.get("why", "")
    if choice == "課題にする":
        if not due:
            raise ValueError("課題にするには締切が要る")
        promote(data, item_id, due, now, via, only)
    elif choice in ("もう済んだ", "要らない"):
        why = f"ユーザー: {choice}" + (f"（{note}）" if note else "")
        dismiss(data, item_id, why, now, via, only)
    else:
        move(data, item_id, CHOICES[choice], now, via, only)
    entry = {"at": now, "id": item_id, "choice": choice, "note": note,
            "due": due.isoformat() if due else None, "source_key": source_key(item_id),
            **{k: item.get(k) for k in ("course", "title", "source", "origin", "certainty", "quote",
                                        "due_basis", "posted", "raw")},
            "due_shown": item.get("due"), "why_shown": shown}
    record(data, entry)
    consolidate(data)  # 答えたものと同じ内容の質問は、同じ扱いにして、もう出さない
    return entry


def record(data, entry):
    """判断の記録。tasks.json の decisions に、置き場所の変更と一緒に保存する。"""
    data.setdefault("decisions", []).append(entry)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    now = datetime.now(JST).isoformat(timespec="seconds")
    with locked_tasks() as data:
        try:
            if cmd == "dismiss":
                _, x = dismiss(data, sys.argv[2], " ".join(sys.argv[3:]), now)
                print(f"片付けた: {x['title']}")
            elif cmd == "promote":
                try:
                    due = datetime.fromisoformat(sys.argv[3])
                except (IndexError, ValueError):
                    raise SystemExit("締切は ISO 8601 で（例 2026-01-15T09:00:00+09:00）")
                _, x = promote(data, sys.argv[2], due, now)
                print(f"課題一覧へ: {x['title']}")
            elif cmd == "consolidate":
                print(f"同じ内容・重複の質問を片付けた: {consolidate(data)} 件")
            else:
                for key in LISTS:
                    for x in data.get(key, []):
                        print(f"[{key}] {x.get('id')}  {x.get('course')}  {x.get('title')}  締切 {x.get('due')}")
        except (LookupError, ValueError) as e:
            raise SystemExit(str(e))


if __name__ == "__main__":
    main()
