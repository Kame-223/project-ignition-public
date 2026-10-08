"""課題ごとのノート（vault/tasks/課題名.md）。見る・作る・チェックを付ける。

本文の書き直しは Obsidian で行う。ここで書くのは「ひな形を作る」と「チェックの1行」だけ。
ファイルの場所は課題の id から決める（画面から届いた文字をパスに使わない）。
"""
import contextlib
import fcntl
import hashlib
import os
import pathlib
import re
import tempfile
import os as _os

BASE = pathlib.Path(__file__).resolve().parent.parent
NOTES = BASE / "vault" / "tasks"
VAULT_NAME = _os.environ.get("OBSIDIAN_VAULT", "my-vault")  # Obsidian で開くときの vault の名前
BAD = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
CHECK = re.compile(r"^(\s*[-*]\s+)\[([ xX])\](\s.*|)$")
MAX = 200_000


def _clean(name):
    s = BAD.sub(" ", name or "").strip(" .")
    return re.sub(r"\s+", " ", s)[:60] or "無題"


def _front_id(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if m:
        for ln in m.group(1).splitlines():
            if ln.startswith("id:"):
                return ln[3:].strip().strip("\"'")
    return None


def find(tid):
    """課題 id のノートのパス（無ければ None）。ファイルの先頭の id: で探す。"""
    if not NOTES.is_dir():
        return None
    for p in sorted(NOTES.glob("*.md")):
        try:
            if _front_id(p.read_text(encoding="utf-8")[:2000]) == tid:
                return p
        except OSError:
            continue
    return None


LOCK = BASE / "data" / ".note.lock"


@contextlib.contextmanager
def _locked():
    """アプリ（全体画面・札の act.py）の間で、ノートの書き込みを1つずつにする。"""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _edit(p, fn):
    """読む → fn で直す → 保存。保存の直前にもう一度読み、Obsidian などで変わっていたら、読み直して最初からやり直す（最大4回）。
    fn(全文) は新しい全文を返す（変えないなら同じ文）。変えたら True。"""
    with _locked():
        for _ in range(4):
            raw = p.read_text(encoding="utf-8")
            new = fn(raw)
            if new == raw:
                return False
            if p.read_text(encoding="utf-8") != raw:
                continue
            _write_atomic(p, new)
            return True
    raise ValueError("ノートが別の所で直されています。もう一度試してください")


def _write_atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".md")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _one_line(s):
    return re.sub(r"\s+", " ", str(s)).strip()


def template(task, done=0):
    """ひな形。子タスクは、課題の stepsを最初から入れる。
    done 件目までは済み（札の ✓ の数）。作ったあとは、ノートが正本（steps を作り直しても、ノートは書き換えない）。"""
    nxt = (task.get("next_action") or "").strip()
    steps = [_one_line(x) for x in (task.get("steps") or []) if _one_line(x)]
    checks = "\n".join(f"- [{'x' if i < done else ' '}] {x}" for i, x in enumerate(steps)) or "- [ ] "
    return (
        f"---\nid: {task['id']}\ncourse: {task.get('course', '')}\n---\n"
        f"# {task.get('title', '')}\n\n"
        f"## 進め方\n{nxt}\n\n"
        f"## 子タスク\n{checks}\n\n"
        "## メモ\n\n"
        "## AI が管理する欄\n"
    )


def create(task, done=0):
    """ひな形を作る（すでにあれば何もしない）。パスを返す。同じ名前のファイルがあっても、絶対に上書きしない（排他的に作る）。"""
    with _locked():
        p = find(task["id"])
        if p:
            return p
        base = _clean(task.get("title"))
        h = hashlib.sha1(str(task["id"]).encode()).hexdigest()[:8]  # 課題 id の全体から（先頭20字の一致で衝突しない）
        text = template(task, done)
        NOTES.mkdir(parents=True, exist_ok=True)
        for cand in [f"{base}.md", f"{base} ({h}).md"] + [f"{base} ({h}-{n}).md" for n in range(2, 50)]:
            path = NOTES / cand
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        raise ValueError("ノートの名前が決められません")


def view(task):
    """画面に出す形。{exists, name, lines:[{t, kind, n?, on?}], url}。"""
    p = find(task["id"])
    if not p:
        return {"exists": False, "title": task.get("title", "")}
    text = p.read_text(encoding="utf-8")[:MAX]
    body = re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S)
    lines, n = [], 0
    for ln in body.splitlines():
        m = CHECK.match(ln)
        if m:
            lines.append({"kind": "check", "n": n, "on": m.group(2) in "xX", "t": m.group(3).strip()})
            n += 1
        elif ln.startswith("#"):
            lines.append({"kind": "h", "t": ln.lstrip("#").strip()})
        else:
            lines.append({"kind": "p", "t": ln})
    return {"exists": True, "title": task.get("title", ""), "name": p.stem, "lines": lines, "mtime": p.stat().st_mtime}


def toggle(task, n, on, expect):
    """n 番目のチェックボックスの1行だけを書き換える。直前に読み直し、画面で見た文字と違えば断る。"""
    p = find(task["id"])
    if not p:
        raise LookupError("ノートがありません")

    def fn(raw):
        lines = raw.split("\n")
        k = -1
        for i, ln in enumerate(lines):
            m = CHECK.match(ln)
            if not m:
                continue
            k += 1
            if k == n:
                if m.group(3).strip() != (expect or "").strip():
                    raise ValueError("ノートが変わっています。開き直してください")
                lines[i] = f"{m.group(1)}[{'x' if on else ' '}]{m.group(3)}"
                return "\n".join(lines)
        raise ValueError("ノートが変わっています。開き直してください")
    _edit(p, fn)


AI_HEAD = "## AI が管理する欄"


def _section(lines):
    """AI 欄の（見出しの行, 本文の始め, 本文の終わり）。見出しが無ければ None。"""
    h = next((i for i, ln in enumerate(lines) if ln.strip() == AI_HEAD), None)
    if h is None:
        return None
    e = next((j for j in range(h + 1, len(lines)) if re.match(r"^#{1,6}\s", lines[j])), len(lines))
    return h, h + 1, e


def get_ai(task):
    """AI 欄の本文（前後の空行なし）。ノートか見出しが無ければ None。"""
    p = find(task["id"])
    if not p:
        return None
    lines = p.read_text(encoding="utf-8").split("\n")
    sec = _section(lines)
    return None if sec is None else "\n".join(lines[sec[1]:sec[2]]).strip()


def set_ai(task, text, expect=None):
    """AI 欄の本文だけを置き換える。expect があれば、今の本文がそれと同じときだけ。直す前の本文を返す。"""
    p = find(task["id"])
    if not p:
        raise LookupError("ノートがありません")
    out = {}

    def fn(raw):
        lines = raw.split("\n")
        sec = _section(lines)
        if sec is None:
            raise ValueError("ノートに「AI が管理する欄」の見出しがありません")
        before = "\n".join(lines[sec[1]:sec[2]]).strip()
        if expect is not None and before != expect.strip():
            raise ValueError("AI 欄が、そのあと変わっています")
        out["before"] = before
        body = text.strip().split("\n") if text.strip() else []
        lines[sec[1]:sec[2]] = body + [""]  # 次の見出しの前の空行（最後の欄なら、ファイルの終わりの改行）
        return "\n".join(lines)
    _edit(p, fn)
    return out["before"]


DEFAULT_MIN = 15  # ノートで足した子タスクの見込み（仮）。元の steps と同じ文の行は、元の分数を使う


def _index():
    """課題 id → ノートのパス（先頭の id: で1回だけ全部読む）。"""
    out = {}
    if NOTES.is_dir():
        for p in NOTES.glob("*.md"):
            try:
                i = _front_id(p.read_text(encoding="utf-8")[:2000])
            except OSError:
                continue
            if i and i not in out:
                out[i] = p
    return out


def items(path):
    """ノートの子タスク [(済みか, 文)]。文が空の行は数えない。"""
    out = []
    for ln in path.read_text(encoding="utf-8")[:MAX].split("\n"):
        m = CHECK.match(ln)
        if m and _one_line(m.group(3)):
            out.append((m.group(2) in "xX", _one_line(m.group(3))))
    return out


def overlay(tasks, state):
    """ノートがある課題は、ノートを正本にする。読んだ課題と state を、メモリの中だけで書き換える。
    札・全体画面・メニューバーはみな core.load() から読むので、ここ1か所で全部にそろう（ディスクの tasks.json・state.json は変えない）。
      steps      = 済みの行 → 未済の行（ノートの並びのまま）。済みの数を state["step"] にすると、札は「最初の未済の行」を最初の一歩に出す
      minutes    = 元の steps と同じ文の行は元の分数、足した行は DEFAULT_MIN。元に分数が無い課題は付けない
    子タスクが1行も無いノートは、今までどおり（steps のまま）。"""
    idx = _index()
    if not idx:
        return
    step = state.setdefault("step", {})
    for t in tasks:
        p = idx.get(t.get("id"))
        if not p:
            continue
        try:
            its = items(p)
        except OSError:
            continue
        if not its:
            continue
        done = [x for c, x in its if c]
        todo = [x for c, x in its if not c]
        order = done + todo
        src = dict(t.get("steps_src") or {})
        ms = src.get("minutes")
        if ms:
            orig = [_one_line(x) for x in (t.get("steps") or [])]
            by = {}
            for i, x in enumerate(orig):
                if i < len(ms) and x not in by:
                    by[x] = ms[i]
            src["minutes"] = [int(by.get(x, DEFAULT_MIN)) for x in order]
            t["steps_src"] = src
        t["steps"] = order
        t["_from_note"] = True  # 子タスクがノート由来（置いた枠の s0/s1 は元の並びの番号なので、途中の行のチェックでずれる。compute_plan が枠を消さないようにする）
        state.setdefault("_note_sig", {})[t["id"]] = hashlib.sha1("\n".join(f"{int(c)}{x}" for c, x in its).encode()).hexdigest()[:10]  # 行の追加・書き換えも「変わった」と分かるように
        step[t["id"]] = len(done)
        if todo:
            t["next_action"] = todo[0]


def check_next(tid):
    """札の ✓ で、ノートの最初の未済の行にチェックを付ける（ノートが無い・未済が無いときは何もしない）。付けたら True。"""
    p = _index().get(tid)
    if not p:
        return False

    def fn(raw):
        lines = raw.split("\n")
        for i, ln in enumerate(lines):
            m = CHECK.match(ln)
            if m and m.group(2) == " " and _one_line(m.group(3)):
                lines[i] = f"{m.group(1)}[x]{m.group(3)}"
                return "\n".join(lines)
        return raw
    return _edit(p, fn)


def read_text(task):
    """ノートの本文（AI に渡す用。無ければ空）。"""
    p = find(task["id"])
    if not p:
        return ""
    return re.sub(r"\A---\n.*?\n---\n", "", p.read_text(encoding="utf-8")[:MAX], count=1, flags=re.S)


def obsidian_url(task):
    from urllib.parse import quote
    p = find(task["id"])
    if not p:
        return ""
    return "obsidian://open?vault=" + quote(VAULT_NAME) + "&file=" + quote(f"tasks/{p.stem}")
