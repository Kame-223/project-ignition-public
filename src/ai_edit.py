"""話す欄の AI が予定・タスクを直すときの「指示」の読み取りと検査（入出力はしない）。

ユーザーの決定: AI は直接ファイルを書かない。決まった種類の指示を返し、サーバーが検査して実行する。
アプリの中はその場で反映（直したあとに「直しました。元に戻す」を出す）。Google カレンダーへの反映は、検査を受けてから（まだ書く道は無い）。
AI の返事の最後に、次の形で指示を付ける:

```ignition-actions
[{"op": "light", "on": true}, {"op": "move", "key": "reading-log-3#1", "start": "20:00", "min": 60}]
```

指示の中の id・key は、状態に書いてあるものだけ。外から来た文字（課題名など）に従って作らない。
"""
import json
import re
import unicodedata
from datetime import datetime

MARK = "ignition-actions"
BLOCK = re.compile(r"```" + MARK + r"\s*\n(.*?)\n?```", re.S)
OPEN = re.compile(r"```" + MARK + r"\b")
MAX_ACTIONS = 6
HM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
ADJ = {"later", "skip", "pin", "reset", "longer", "shorter", "today"}

# op → {欄: 種類}。種類: id（文字）・hm（HH:MM）・hm24（HH:MM か 24:00）・int（整数）・bool・adj・due（ISO＋タイムゾーンか null）・text
SPEC = {
    "replan": {},
    "light": {"on": "bool"},
    "late": {"on": "bool"},
    "move": {"key": "id", "start": "hm", "min": "int"},
    "create": {"title": "text", "start": "hm", "min": "int"},
    "place": {"id": "id", "start": "hm"},
    "unplace": {"key": "id"},
    "adjust": {"id": "id", "action": "adj"},
    "remove_adhoc": {"id": "id"},
    "fixed": {"key": "id", "start": "hm", "end": "hm24"},
    "task_due": {"id": "id", "due": "due"},
    "task_title": {"id": "id", "title": "text"},
    "task_done": {"id": "id"},
    "review_decide": {"id": "id", "choice": "rchoice"},  # 要確認の質問への答えで、扱いがはっきりしたとき（回答済みに残り、答え直しで戻せる）
    "ignore_course": {"code": "code"},  # 「取っていない」と言われた科目を、集めない（courses.json の ignored）
    "note_set": {"text": "note"},  # 開いているノートの「AI が管理する欄」だけの書き換え（どのノートかはユーザーが開いているもの。AI は選べない）
    "task_create": {"title": "text", "course": "id", "due": "due", "min": "int"},  # 新しい課題を作る（course は科目名。分からなければ「大学」。due は null 可）
    "plan_reset": {},  # 今日の予定を「PLAN TODAY を押す前」に戻す（予定を全部外し、自動で組まない状態）
    "order_after": {"id": "id", "after": "id"},  # 「AはBが終わってから」（自動で組むとき、A を B の後ろにする）
    "remember": {"about": "id", "text": "text"},  # ユーザーの決まり・好みを、覚えておく（科目名か「ユーザー」。facts に残る）
    "tutorial_log": {"text": "text"},  # 個別面談管理ファイルの「状態の記録」に、ユーザーの言葉を1行足す（申し込んだ・受けた など）
    "undo_last": {},  # 直前の AI の直しを元に戻す（ほかの指示とは一緒に出せない）
    "task_estimate": {"id": "id", "min": "int"},  # ユーザーが言った合計の見込み（分）。「2時間ぐらいかかる」
    "event_create": {"title": "text", "start": "iso", "end": "iso", "kind": "ekind"},  # 日付つきの予定（明日・来週も）。種類は school（学校の用事）か personal（個人）。授業は変わらないので授業は作らない
    "event_remove": {"id": "id"},  # アプリで作った予定だけ消せる（カレンダー由来・授業は消せない）
    "material_search": {"query": "text", "course": "mcourse"},  # 授業の資料・シラバス・授業サイト の投稿から探す（読むだけ。bin/class_info.py）。見つかった所の文が AI に渡る
    "dev_request": {"goal": "text"},  # アプリそのもの（画面・コード）を直す頼み。別の作業場所で改造役が直し、関門（テスト・審査）を通ったら、ユーザーの「適用」で本番に入る（bin/dev_agent.py）
    "portal_check": {"page": "portal"},  # ユーザーが「ポータルで確認して」と言ったとき。ユーザーの Chrome（サインイン済み）で許可ページを読む（読むだけ。bin/portal.py）
    "task_label": {"id": "id", "label": "text"},  # 課題の「画面に出す名前」だけを変える（正式な題は変えない。data/labels.json）
    "task_reopen": {"id": "id"},  # 完了を未完了に戻す
}
PLAN_OPS = {"replan", "light", "late", "move", "create", "place", "unplace", "adjust", "remove_adhoc", "fixed", "plan_reset", "order_after"}
EVENT_OPS = {"event_create", "event_remove"}
DEV_WORDS = re.compile(r"(アプリ|画面|機能|ボタン|表示|見た目|デザイン|札|時間軸|話す欄|システム)")
DEV_VERBS = re.compile(r"(変え|直し|足し|追加|作っ|なおし|変更|改善|付け|つけ|消し|増や|減ら|してほしい|して欲しい|にして|できるように|ように)")
DEV_NO = re.compile(r"(ないで|やめて|いらない|要らない|不要)")
MATERIAL_COURSES = {"COURSE_A", "COURSE_B"}
MATERIAL_HINT = re.compile(r"(調べ|調査|探|確認|読んで|教えて|どんな|何の|何を|資料|内容|詳しく|見て|知りたい|？|\?)")
PORTAL_PAGES = {"current", "dashboard", "lms", "lms_grades"}
PORTAL_WORDS = re.compile(r"(ポータル|ポータ|lms|LMS|LMS|成績|グレード)", re.I)
PORTAL_VERBS = re.compile(r"(確認|見て|見せ|見に|調べ|読んで|チェック|確かめ|探し|あるか|出てる|載って)")
PORTAL_NO = re.compile(r"(見ないで|確認しないで|調べないで|読まないで|しなくていい|しなくてよい|いらない|要らない)")
MEMO_OPS = {"remember", "tutorial_log"}
TASK_OPS = {"task_label", "task_due", "task_title", "task_done", "task_reopen", "task_estimate", "task_create"}
NOTE_OPS = {"note_set"}
REVIEW_OPS = {"review_decide", "ignore_course"}
RCHOICE = {"課題にする", "要らない", "もう済んだ", "先の予定", "いつもの決まり"}
CODE = re.compile(r"^[A-Za-z0-9&]{2,12}$")
NOTE_MAX = 2000
NOTE_BAD = re.compile(r"^\s{0,3}(#|---|\+\+\+)", re.M)  # 見出し・区切りを混ぜて、ユーザーの欄の並びを壊させない


def parse(text):
    """返事から指示の塊を取り出す。返り値: (指示の塊を除いた本文, 指示のリスト or None, 読めなかった理由 or None)。"""
    text = text or ""
    ms = list(BLOCK.finditer(text))
    if not ms:
        if OPEN.search(text):  # 開いたのに閉じていない（途中で切れた）
            return text[:OPEN.search(text).start()].rstrip(), None, "指示が途中で切れていたので、何も直していません"
        return text, None, None
    clean = BLOCK.sub("", text).strip()  # 塊は全部、本文から外す（記録・次の入力に残さない）
    if len(ms) > 1:  # 複数ある・引用のように途中にある塊は、実行しない
        return clean, None, "指示の塊が複数あったので、何も直していません"
    m = ms[0]
    if text[m.end():].strip():
        return clean, None, "指示が返事の最後になかったので、何も直していません"
    try:
        acts = json.loads(m.group(1))
    except ValueError:
        return clean, None, "指示の形が読めなかったので、何も直していません"
    if not isinstance(acts, list):
        return clean, None, "指示の形が読めなかったので、何も直していません"
    return clean, acts, None


def validate(acts):
    """指示を検査する。返り値: (使える指示のリスト, 断った理由のリスト)。1つでも不正なら、全部使わない（半分だけ直さない）。"""
    if not acts:
        return [], []
    if len(acts) > MAX_ACTIONS:
        return [], [f"指示が多すぎます（{MAX_ACTIONS}個まで）"]
    out, errs = [], []
    for i, a in enumerate(acts, 1):
        if not isinstance(a, dict) or not isinstance(a.get("op"), str) or a["op"] not in SPEC:
            errs.append(f"{i}番目: 知らない種類の指示")
            continue
        spec, clean = SPEC[a["op"]], {"op": a["op"]}
        extra = set(a) - set(spec) - {"op", "from"}
        if extra:
            errs.append(f"{i}番目({a['op']}): 余計な欄 {sorted(extra)}")
            continue
        for k, kind in spec.items():
            if k not in a:  # 欠けた欄を null（締切の削除など）として受け取らない
                errs.append(f"{i}番目({a['op']}): {k} が無い")
                break
            v = a[k]
            ok = False
            if kind == "id":
                ok = isinstance(v, str) and 0 < len(v) <= 200
            elif kind == "mcourse":
                ok = isinstance(v, str) and (v == "all" or v in MATERIAL_COURSES)
            elif kind == "iso":
                try:
                    ok = isinstance(v, str) and datetime.fromisoformat(v).tzinfo is not None
                except ValueError:
                    ok = False
            elif kind == "ekind":
                ok = isinstance(v, str) and v in ("school", "personal")
            elif kind == "portal":
                ok = isinstance(v, str) and v in PORTAL_PAGES
            elif kind == "rchoice":
                ok = isinstance(v, str) and v in RCHOICE
            elif kind == "code":
                ok = isinstance(v, str) and bool(CODE.match(v))
            elif kind == "note":
                ok = isinstance(v, str) and 0 < len(v.strip()) <= NOTE_MAX and not NOTE_BAD.search(v) and "\x00" not in v
                v = v.strip() if ok else v
            elif kind == "text":
                ok = isinstance(v, str) and 0 < len(v.strip()) <= 200
                v = v.strip() if ok else v
            elif kind == "hm":
                ok = isinstance(v, str) and bool(HM.match(v))
            elif kind == "hm24":
                ok = isinstance(v, str) and (bool(HM.match(v)) or v == "24:00")
            elif kind == "int":
                ok = isinstance(v, int) and not isinstance(v, bool) and 5 <= v <= 12 * 60
            elif kind == "bool":
                ok = isinstance(v, bool)
            elif kind == "adj":
                ok = isinstance(v, str) and v in ADJ
            elif kind == "due":
                if v is None:
                    ok = True
                elif isinstance(v, str):
                    try:
                        ok = datetime.fromisoformat(v).tzinfo is not None  # タイムゾーンが要る（+09:00）
                    except ValueError:
                        ok = False
            if ok and kind in ("hm", "hm24") and isinstance(v, str) and v != "24:00" and int(v[3:]) % 5:
                ok = False  # 5分刻みだけ（黙って丸めない）
            if ok and kind == "int" and v % 5:
                ok = False
            if ok and a["op"] in ("create", "task_create") and k == "title" and len(v) > 80:
                ok = False  # 保存の上限（80字）を、検査でそろえる
            if not ok:
                errs.append(f"{i}番目({a['op']}): {k} の値が不正" + ("（見出しの # や --- の行、2000字超、空は使えません）" if kind == "note" else ""))
                break
            clean[k] = v
        else:
            if isinstance(a.get("from"), str) and a["from"].strip():  # ユーザーの発言のどこから読み取ったか（原文の引用）。形が悪くても指示は落とさない
                clean["from"] = a["from"].strip()[:200]
            out.append(clean)
    if errs:
        return [], errs
    return out, []


# 課題を直す指示は、ユーザーの今の発言に、それらしい言葉があるときだけ実行する（外の文字に誘導されて、頼んでいない完了・改名が起きないように）
INTENT = {
    "task_done": ("完了", "終わっ", "終わり", "済", "できた", "やった", "done"),
    "task_reopen": ("未完了", "戻", "まだ", "やり直", "取り消", "解除"),
    "task_due": ("締切", "締め切り", "期限", "しめきり", "デッドライン", "まで"),
    "review_decide": ("課題", "要らない", "いらない", "不要", "済", "先の予定", "いつもの", "決まり", "ではない", "関係な", "大丈夫", "だいじょうぶ", "問題な", "対応しな", "気にしな", "スルー", "もういい", "いいよ", "これでいい", "終わっ", "終わり", "やった", "OK", "ok", "オッケー"),  # 「これは大丈夫」＝対応不要
    "ignore_course": ("取っていない", "とっていない", "履修していない", "履修してない", "流さない", "流さないで", "要らない", "いらない", "不要", "関係な", "取らない"),
    "note_set": ("ノート", "書", "まとめ", "分け", "分解", "整理", "メモ", "追記", "入れ", "作って", "出して", "考えて", "洗い出", "計画", "手順", "子タスク"),
    "task_create": ("課題", "タスク", "入れ", "追加", "作", "保存", "やらなきゃ", "やる"),
    "task_estimate": ("時間", "分", "かかる", "掛かる", "見込み", "ぐらい", "くらい", "日"),
    "event_create": ("予定", "入れ", "いれ", "から", "まで", "会", "遊", "行く", "行き", "電話", "約束", "飲み", "食事", "ごはん"),
    "event_remove": ("予定", "消", "取り消", "やめ", "キャンセル", "なし", "外し"),
    "task_label": ("表示", "表記", "名前", "呼", "ラベル", "英語", "日本語", "短く", "略"),
    "task_title": ("名前", "題", "タイトル", "変え", "直し", "改名", "リネーム"),
}


STRICT_WORDS = {"ignore_course", "event_remove"}
BULK_OK = re.compile(r"(全部|すべて|全て|まとめて|両方|それぞれ|みんな|どれも)")
MAX_TARGETS = 3  # 1回の発言で変えてよい課題・予定の数（「全部」などと言ったときを除く）


COMPOSE_WORDS = {
    "remember": re.compile(r"(覚え|記憶|ルール|これから|今後|決まり|メモ|記録|ずっと|いつも)"),
    "tutorial_log": re.compile(r"(申し込|予約|受け|個別面談|面談|済ませ|終わっ)"),
    "replan": re.compile(r"((?<!取り)組|PLAN|プラン|スケジュール|予定を立て|立て直)", re.I),
    "light": re.compile(r"(軽|少なく|半分|楽に|ゆるく)"),
    "late": re.compile(r"(24時|２４時|夜遅く|遅くまで|深夜|12時まで|０時)"),
}
READ_ONLY = re.compile(r"(教えて|見せて|見たい|確認したい|どれ|何が|なにが|いつ|どこ|どう|ありますか|あるの|できるの|できる？|わかる|分かる|とは|って何|って？)")
REQUEST_WORDS = re.compile(r"(して|してほしい|欲しい|ほしい|お願い|ください|戻して|入れて|保存|作って|変えて|直して|消して|外して|終わらせ|やりたい|組んで|出して|書いて|決めて|進めて|にする|にして|取り消|やめて|外れて|完了)")


NEGATION = re.compile(r"^[^。！？!?\n]{0,8}?(ないで|ないでね|なくていい|なくて[いよ]|ません|ない(?:です|よ|で|。|$)|やめ|不要|いらない|要らない|まだ|いない)")


def _affirmed(rx, text):
    """言葉があり、そのすぐ後ろが否定（しないで・やめて・まだ…ない）ではない所が1つでもあるか。"""
    for m in rx.finditer(text or ""):
        if not NEGATION.match(text[m.end():]):
            return True
    return False


def _read_only(text):
    """調べる・尋ねるだけの発言か（変える頼みの言葉が無く、尋ねる形）。"""
    t = (text or "").strip()
    if REQUEST_WORDS.search(t):
        return False
    return bool(READ_ONLY.search(t)) or t.endswith(("？", "?"))


def own_words(text):
    """ユーザーの発言のうち、ユーザーが書いた部分だけ。質問への回答の形（【質問への回答】質問：…／私の回答：…）では、
    質問の文（メール・授業サイト など外から来たもの）を除き、「私の回答：」の後ろだけを見る。"""
    t = text or ""
    if t.startswith("【質問への回答】"):
        i = t.rfind("\n私の回答：")  # 最後の「私の回答：」から（質問の文＝外から来た文に「私の回答：」が混ざっていても、ユーザーの回答の位置を取り違えない）
        if i < 0:
            return ""
        t = t[i + len("\n私の回答："):]
        j = t.rfind("\n（質問ID:")
        return t[:j] if j >= 0 else t
    return t


PUNCT = re.compile(r"[\s、。，．,.！？!?「」『』()・…~〜ー\-]")


def _norm(s):
    return PUNCT.sub("", unicodedata.normalize("NFKC", s or "")).lower()


def grounded(quote, own):
    """AI が『ユーザーの発言のここから読み取った』と引用した部分が、ユーザーが書いた部分に実在するか（言い方・語は問わない）。
    外から来た文（メール・資料・道具の結果）から引用した指示は、ここで落ちる。"""
    q = _norm(quote)
    return len(q) >= 2 and q in _norm(own)


def intent_ok(op, user_text, strict=False, quote=None):
    """ユーザーが打った言葉なら、言い方で弾かない。
    語で確かめるのは、質問への回答の形（質問の文にメール・授業サイト など外から来た文が混ざる）だけ。動いた分は、どれも元に戻せる。
    strict: AI が道具で外から来た文（資料・メール・課題名）を読んだあとの返事。そのときは、回答の形と同じく、ユーザーの言葉に操作それぞれの語があるときだけ通す
    （読んだ文に誘導された変更を通さない。レビュー）。"""
    if quote and grounded(quote, own_words(user_text)):  # 語の一致ではなく、引用がユーザーの言葉にあるかで決める
        if op == "dev_request":
            return not DEV_NO.search(quote)
        if op in ("portal_check", "material_search"):
            return not PORTAL_NO.search(quote)
        if op in ("replan", "light", "late"):
            return not re.search(r"(ないで|やめて|しなくて|不要)", quote)
        return True
    if op == "material_search":  # 読むだけの操作。ユーザーが調べたいと言った（尋ねた）ときだけ
        t = own_words(user_text)
        return bool(MATERIAL_HINT.search(t)) and not PORTAL_NO.search(t)
    if op == "dev_request":  # アプリを直す頼みは、ユーザーの言葉に「アプリ・画面・機能」などと「変えて・足して」などがあるときだけ（尋ねただけ・否定では動かない）
        t = own_words(user_text)
        return bool(DEV_WORDS.search(t)) and bool(DEV_VERBS.search(t)) and not DEV_NO.search(t) and not _read_only(t)
    if op == "portal_check":  # ユーザーが「ポータル」「学習管理システム」と言ったときだけ（外の文に誘導されて、Chrome を読みに行かない）
        t = own_words(user_text)
        return bool(PORTAL_WORDS.search(t)) and bool(PORTAL_VERBS.search(t)) and not PORTAL_NO.search(t)
    if op in COMPOSE_WORDS:  # 予定を組む操作は、組む・軽く・24時までの頼みがあるときだけ（。相談に勝手に組まない）
        if not _affirmed(COMPOSE_WORDS[op], own_words(user_text)):
            return False
    if op in STRICT_WORDS and not any(w in own_words(user_text) for w in INTENT[op]):  # 科目を集めない・予定を消す、は言い方を問わず、それらしい言葉があるときだけ（外から来た文に誘導されない）
        return False
    if not (user_text or "").startswith("【質問への回答】"):
        if _read_only(user_text):  # 「今日の課題を教えて」のような調べる・尋ねるだけの発言では、何も変えない
            return False
        if not strict:
            return True
    user_text = own_words(user_text)
    words = INTENT.get(op)
    if words is None:
        return True
    t = user_text or ""
    return any(w in t for w in words)
