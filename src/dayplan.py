"""今日を組む論理（全体画面・札が同じものを読む）。入出力はしない（テストしやすいように）。

ユーザーの決まり（決定の記録）:
  - 開くと、その場で自動で組まれる。気に入らなければ変える
  - 時間通りにいかないと自動で直る。直すのはユーザーが押したとき（▶︎・■・完了・今から・変える）。時計が進んだだけでは動かさない
  - プロジェクトの一手は、必要な日だけ今日に入る: 始める日が来ていて、近い締切の課題を置いたあとに空きがある日
初期値（使いながら見直す）:
  - 空き時間の半分まで・1日5件まで・課題の間15分・授業の直後10分・区切りは25〜50分のコマにまとめる
  - 長さが分からない課題は、30分の「まず確かめる」コマにする（0分として消さない）
"""
from datetime import datetime, timedelta

DAY_END = "22:00"
DAY_START = "06:00"  # 自動で予定を組むのは 6時〜22時
GAP_TASK = 15
GAP_CLASS = 10
BUDGET = 0.5
MAX_TASKS = 5
UNKNOWN_MIN = 30
URGENT_H = 36  # 締切がこれより近いものは、量の上限（BUDGET・MAX_TASKS）を越えても置く（入る場所があれば）
LEN_STEP = 15  # 「長さ±」の1回ぶん


def apply_after(order, overrides):
    """「AはBが終わってから」（overrides[A]["after"] = B）。A が B より前にあれば、B の直後へ移す。
    B が今日の候補に無ければ何もしない。順に1回ずつ直す（輪になっても止まる）。"""
    order = list(order)
    for _ in range(len(order) + 1):  # 連鎖（A→B→D）も整うまで繰り返す。輪になっても、回数で止まる
        moved = False
        for t in list(order):
            b = (overrides.get(t["id"]) or {}).get("after")
            if not b or b == t["id"]:
                continue
            ids = [x["id"] for x in order]
            if b not in ids or t["id"] not in ids:
                continue
            i, j = ids.index(t["id"]), ids.index(b)
            if i < j:
                order.insert(j, order.pop(i))  # pop で i が空くので、b の直後は j の位置
                moved = True
        if not moved:
            break
    return order


def mins_of(task, done=0):
    """残りの区切りの分数（見込み）。分からなければ None。"""
    src = task.get("steps_src") or {}
    ms = src.get("minutes") if isinstance(src, dict) else None
    steps = task.get("steps") or []
    user_total = task.get("estimate_user")  # ユーザーが言った、この課題の合計の見込み（分）。区切りごとの分数を、合計に合わせて拡げる
    ok_total = isinstance(user_total, (int, float)) and not isinstance(user_total, bool) and user_total > 0
    if ok_total and ms and steps and len(ms) >= len(steps) and sum(ms) > 0:
        k = float(user_total) / sum(ms)
        ms = [max(5, int(round(m * k / 5.0)) * 5) for m in ms]
    elif ok_total and (not steps or not ms or len(ms) < len(steps)):  # 区切りが無い・区切りごとの分数が無い課題は、ユーザーが言った合計をそのまま（区切りがあれば、残りの割合で）
        rest = 1.0 if not steps else max(0, len(steps) - done) / len(steps)
        return [max(5, int(round(user_total * rest / 5.0)) * 5)] if rest > 0 else None
    if not ms or (steps and len(ms) < len(steps)):
        est = task.get("estimate_min")  # 区切りが無い用事（「今から」で入れたものなど）は、見込みの分数だけ
        return [int(est)] if isinstance(est, (int, float)) and est > 0 and not steps else None
    return list(ms[done:]) or None


def free_slots(start, end, busy):
    """start〜end の空き（授業などの直後 GAP_CLASS 分は空ける）。end を越えない。"""
    slots, cur = [], start
    for bs, be in sorted(busy):
        if bs >= end:
            break
        if be <= cur:
            cur = max(cur, be + timedelta(minutes=GAP_CLASS)) if be > start - timedelta(minutes=GAP_CLASS) else cur
            continue
        if bs > cur:
            slots.append([cur, min(bs, end)])
        cur = max(cur, be + timedelta(minutes=GAP_CLASS))
    if cur < end:
        slots.append([cur, end])
    return [s for s in slots if s[1] > s[0]]


ONE_BLOCK_MAX = 120  # 1つの課題はこの長さまで1枠にまとめる


def blocks_of(ms, first_index=0, cap=50):
    """区切りの分数を、25〜cap 分のコマにまとめる。[(区切りの始め, 終わり, 分)]。
    区切りは割らないが、1つの区切りが cap を越えるときは時間で割る（1枠が長くなりすぎない）。"""
    out = _blocks_of(ms, first_index, cap)
    res = []
    for s0, s1, m in out:
        while m > max(cap, ONE_BLOCK_MAX if cap >= ONE_BLOCK_MAX else cap):
            step = cap if cap >= ONE_BLOCK_MAX else cap
            res.append((s0, s1, step))
            m -= step
        res.append((s0, s1, m))
    return res


def _blocks_of(ms, first_index=0, cap=50):
    out, cur, first = [], 0, 0
    for i, m in enumerate(ms):
        if cur and cur + m > cap:
            out.append((first + first_index, i - 1 + first_index, cur))
            first, cur = i, 0
        cur += m
        if cur >= (cap if cap > 50 else 25):
            out.append((first + first_index, i + first_index, cur))
            first, cur = i + 1, 0
    if cur:
        if out and cur < 10 and out[-1][2] + cur <= 60:
            s0, _, m = out.pop()  # 10分未満のしっぽは、前のコマにまとめる（細切れのコマを作らない）
            out.append((s0, len(ms) - 1 + first_index, m + cur))
        else:
            out.append((first + first_index, len(ms) - 1 + first_index, cur))
    return out


def _ceil5(d):
    """5分の目盛りへ切り上げる。"""
    extra = (-(d.minute % 5)) % 5
    return (d + timedelta(minutes=extra)).replace(second=0, microsecond=0) if extra or d.second or d.microsecond else d


def _hm_end(s, e):
    """終わりの時刻。次の日の0時ちょうどは「24:00」（当日の終わり）として返す。"""
    if e.date() != s.date() and e.hour == 0 and e.minute == 0:
        return "24:00"
    return _hm(e)


def _hm(d):
    return d.strftime("%H:%M")


def make_plan(now, classes, tasks, *, done=None, overrides=None, adhoc=None, extra=None, day_end=DAY_END, first=None,
              placed=None, hold=None, unsched=None, max_tasks=None):
    """今日の予定を組む。

    now: 組み始める時刻（datetime、タイムゾーン付き）
    classes: [{"start": datetime, "end": datetime, ...}] 今日の授業
    tasks: 並べる順（core.order の順）の課題
    done: {課題 id: 済んだ区切りの数}
    overrides: {課題 id: {"skip": bool, "later": bool, "len": 分の増減}}
    adhoc: [{"id", "title", "start": "HH:MM", "min"}] 「今から〇〇する」で入れたもの（動かさない）
    extra: 課題のあとに置いてみるもの（プロジェクトの一手など。空きが残れば入る）
    placed: ユーザーが時間軸で動かしたあとの柔軟予定（freeze の形）。その場所のまま置き、自動では動かさない
    hold: {課題 id: 理由} 時間軸から溢れて「今日に入らなかったもの」へ戻したもの。ユーザーが戻すまで自動では置かない
    unsched: {課題 id} 「今日やる・時間未定」。返り値の unsched に出す
    返り値: {"blocks": [...], "left": [{"id", "why"}], "free_min", "budget_min", "used_min"}
    """
    done, overrides, adhoc, extra = done or {}, overrides or {}, adhoc or [], extra or []
    placed, hold, unsched = placed or [], hold or {}, set(unsched or ())
    end = now.replace(hour=int(day_end[:2]), minute=int(day_end[3:]), second=0, microsecond=0)
    busy = [(c["start"], c["end"]) for c in classes]
    fixed = []
    for a in adhoc:
        s = now.replace(hour=int(a["start"][:2]), minute=int(a["start"][3:]), second=0, microsecond=0)
        e = s + timedelta(minutes=int(a["min"]))
        busy.append((s, e))
        fixed.append({"task": a["id"], "key": a["id"], "kind": "adhoc", "title": a["title"], "s0": 0, "s1": 0, "start": _hm(s), "end": _hm_end(s, e)})
    placed_min = 0
    for p in placed:
        s = now.replace(hour=int(p["start"][:2]), minute=int(p["start"][3:]), second=0, microsecond=0)
        e = s + timedelta(minutes=int(p["min"]))
        busy.append((s, e))
        placed_min += int(p["min"])
        fixed.append({"task": p["task"], "key": p["key"], "kind": p.get("kind", "task"), "title": p.get("title", ""),
                      "s0": p.get("s0", 0), "s1": p.get("s1", 0), "part": p.get("part", 1), "parts": p.get("parts", 1),
                      "unknown": p.get("unknown", False), "start": _hm(s), "end": _hm_end(s, e)})
    placed_ids = {p["task"] for p in placed}
    slots = free_slots(now, end, busy)
    free_total = sum((b - a).total_seconds() / 60 for a, b in slots)
    budget = free_total * BUDGET
    running = [t for t in tasks if first and t["id"] == first]  # 作業中のものは何より先（札・時間軸と食い違わない）
    others = [t for t in tasks if not (first and t["id"] == first)]
    pinned = [t for t in others if overrides.get(t["id"], {}).get("pin")]  # ユーザーが「今日に入れる」を押したもの
    rest = [t for t in others if not overrides.get(t["id"], {}).get("pin")]
    order = running + pinned + [t for t in rest if not overrides.get(t["id"], {}).get("later")] + \
        [t for t in rest if overrides.get(t["id"], {}).get("later")]
    order = apply_after(order, overrides)
    placed, left, used, n = list(fixed), [], placed_min, len(placed_ids)
    unsched_out = []
    for t in order + list(extra):
        if t["id"] in placed_ids:  # ユーザーが置いた場所のまま（上で置いた）
            continue
        if t["id"] in unsched:  # 今日やる・時間未定。置かないが、今日やることの一覧には残す
            unsched_out.append(t["id"])
            continue
        if t["id"] in hold:
            left.append({"id": t["id"], "why": hold[t["id"]]})
            continue
        ov = overrides.get(t["id"], {})
        due = t.get("due")
        try:
            urgent = bool(due) and datetime.fromisoformat(due) - now <= timedelta(hours=URGENT_H)
        except (TypeError, ValueError):
            urgent = False
        must = ov.get("pin") or urgent  # ユーザーが入れたもの・締切が近いもの
        if ov.get("skip"):
            left.append({"id": t["id"], "why": "今日はやらない"})
            continue
        if n >= (MAX_TASKS if max_tasks is None else max_tasks) and not must:
            left.append({"id": t["id"], "why": "今日は入りきらない"})
            continue
        ms = t.get("_mins") if "_mins" in t else mins_of(t, done.get(t["id"], 0))
        unknown = ms is None
        if unknown:
            ms = [UNKNOWN_MIN]
        if ov.get("len"):
            ms = list(ms)
            ms[-1] = max(5, ms[-1] + int(ov["len"]))
        base = 0 if unknown else done.get(t["id"], 0)
        need = sum(ms)
        if used + need > budget and not must:  # ユーザーが入れたもの・締切が近いものは、量の上限を越えても置く（入る場所があれば）
            left.append({"id": t["id"], "why": "今日は入りきらない"})
            continue
        dep = ov.get("after")
        dep_end = None
        if dep and dep != t["id"]:
            if any(x["id"] == dep for x in left):  # 先の課題が今日に入らないなら、後の課題も今日は置かない（順番を守る）
                left.append({"id": t["id"], "why": "先にやる課題が今日は入らない"})
                continue
            ends = [hm2m(b["end"]) for b in placed if b.get("task") == dep]
            dep_end = max(ends) if ends else None
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        saved = [s[:] for s in slots]
        tmp, ok = None, False
        # まず大きく1枠（2時間まで）で置いてみる。入らなければ、授業などの前後で分ける（25〜50分）
        for cap in (ONE_BLOCK_MAX, 50):
            slots[:] = [s[:] for s in saved]
            after, tmp, ok = now, [], True
            if dep_end is not None:  # 「AはBが終わってから」: A は B の最後の枠の終わり以降にだけ置く（候補の並びだけでなく、時間でも後ろ）
                after = max(after, midnight + timedelta(minutes=dep_end + GAP_TASK))
            for (s0, s1, m) in blocks_of(ms, base, cap):
                m = -(-m // 5) * 5  # 5分刻みにそろえる
                for sl in slots:
                    a = _ceil5(max(sl[0], after))
                    if (sl[1] - a).total_seconds() / 60 >= m:
                        b = a + timedelta(minutes=m)
                        tmp.append({"task": t["id"], "kind": t.get("_kind", "task"), "title": t.get("title", ""),
                                    "s0": s0, "s1": s1, "start": _hm(a), "end": _hm(b), "unknown": unknown})
                        if a > sl[0]:
                            slots.append([sl[0], a])
                        sl[0] = b + timedelta(minutes=GAP_TASK)
                        after = b + timedelta(minutes=GAP_TASK)
                        slots.sort(key=lambda s: s[0])
                        break
                else:
                    ok = False
                    break
            if ok:
                break
        if not ok:
            slots[:] = saved
            left.append({"id": t["id"], "why": "今日は入りきらない"})
            continue
        for k, x in enumerate(tmp):
            x["part"], x["parts"], x["key"] = k + 1, len(tmp), f"{t['id']}#{k + 1}"  # 分かれたときは画面で「（続き）」と出す
        placed += tmp
        used += need
        n += 1
    placed.sort(key=lambda b: b["start"])
    return {"blocks": placed, "left": left, "unsched": unsched_out, "free_min": round(free_total), "budget_min": round(budget), "used_min": round(used)}


def hm2m(hm):
    return int(hm[:2]) * 60 + int(hm[3:5])


def m2hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def freeze(blocks):
    """組んだ予定（adhoc 以外）を、ユーザーが動かせる形（placed）に写す。ユーザーが時間軸を初めて触ったときに1回。
    以後は自動で組み直しても、写したものの場所は変えない（新しい課題だけが空きに入る）。"""
    out = []
    for b in blocks:
        if b.get("kind") == "adhoc":
            continue
        out.append({"key": b.get("key") or f"{b['task']}#{b.get('part', 1)}", "task": b["task"], "kind": b.get("kind", "task"),
                    "title": b.get("title", ""), "s0": b.get("s0", 0), "s1": b.get("s1", 0), "part": b.get("part", 1),
                    "parts": b.get("parts", 1), "unknown": b.get("unknown", False),
                    "start": b["start"], "min": hm2m(b["end"]) - hm2m(b["start"])})
    return out


def _hit(a0, a1, b0, b1):
    return a0 < b1 and b0 < a1


def settle(items, fixed, end, anchor=None, pushed=()):
    """柔軟予定を重ならないように並べ直す。時刻は0時からの分。

    items: [{"key", "start", "min"}]  柔軟予定（課題・プロジェクトの一手・自分で入れた予定）
    fixed: [(始め, 終わり)]  固定予定（授業・カレンダーの予定）。押されて動かない
    end: これを越えてよけたものは溢れる（22時／「今日は24時まで」なら24時）
    anchor: ユーザーが今動かした・広げた key。その場所から動かさない（固定予定の上でもよい＝横に並べる）
    pushed: 最初から「よけた」扱いにする key
    決まり:
      - anchor を先に置き、ほかは始まりの早い順。先に置いた柔軟予定と重なったら、そのすぐ下へよける（上へは動かさない）
      - よけたものが固定予定にかかったら、固定予定を飛び越えて直後 GAP_CLASS 分あける
      - よけずに済んだものは、固定予定の上にあってもそのまま
      - よけた結果 end を越えたら置かずに溢れさせる（消さない。呼ぶ側が「今日に入らなかったもの」へ戻す）
    返り値: (置けたもの [{"key", "start", "min", "moved"}], 溢れた key のリスト)
    """
    pushed = set(pushed)
    order = sorted(items, key=lambda x: (0 if x["key"] == anchor else 1, x["start"]))
    done, over = [], []
    for it in order:
        t, m = int(it["start"]), int(it["min"])
        moved = it["key"] in pushed
        if it["key"] != anchor:
            while True:
                hit = [d for d in done if _hit(t, t + m, d["start"], d["start"] + d["min"])]
                if hit:
                    t = max(d["start"] + d["min"] for d in hit)
                    moved = True
                    continue
                if moved:
                    fx = [(a, b) for a, b in fixed if _hit(t, t + m, a, b)]
                    if fx:
                        t = max(b for _a, b in fx) + GAP_CLASS
                        continue
                break
            if moved and t + m > end:
                over.append(it["key"])
                continue
        done.append({"key": it["key"], "start": t, "min": m, "moved": moved and t != int(it["start"])})
    return done, over


def project_steps(projects, today_iso, force=()):
    """始める日が来たプロジェクトの次の一手（今日に置いてみるもの）。force はユーザーが「今日に入れる」を押したもの。"""
    out = []
    for p in projects:
        if p.get("start", "9999") <= today_iso or p["id"] in force:
            out.append({"id": "pj:" + p["id"], "title": f"{p['name']}：{p.get('next', '')}", "course": p.get("course", ""),
                        "_mins": [int(p.get("next_min") or UNKNOWN_MIN)], "_kind": "project", "steps": [p.get("next", "")]})
    return out


def free_between(now, until, classes_by_day, day_start="09:00", day_end="22:00"):
    """now から until まで（授業を引いた、9:00〜22:00 の）空きの分数。課題の「締切までに使える時間」に使う。"""
    tot, d = 0, now
    while d.date() <= until.date():
        s = max(d, d.replace(hour=int(day_start[:2]), minute=int(day_start[3:]), second=0, microsecond=0))
        e = min(until, d.replace(hour=int(day_end[:2]), minute=int(day_end[3:]), second=0, microsecond=0))
        if e > s:
            f = (e - s).total_seconds() / 60
            for cs, ce in classes_by_day.get(d.date().isoformat(), []):
                f -= max(0, (min(e, ce) - max(s, cs)).total_seconds() / 60)
            tot += max(0, f)
        d = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return round(tot)
