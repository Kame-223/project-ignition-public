/* デモ用の差し替え: 本物のサーバーの代わりに、架空のデータ（demo_*.json）で画面を動かす。画面の HTML は本物と同じ。
   操作（始める・完了・予定を置く…）は見た目の確認用で、データは変わらない。話す欄は、決まった返事を返す。 */
(function () {
  const N = Date.now.bind(Date);
  const OFF = Date.parse("2026-10-08T12:30:00+09:00") - N();   // 画面の時計を「2026-10-08 12:30」に合わせる（進み方は本物と同じ）
  const D = Date, J = 9 * 3600000;   // 日本時間の表示にそろえる（見る人の端末の時間帯に関わらず）
  class FD extends Date { constructor(...a) { if (a.length === 0) super(N() + OFF); else super(...a); } static now() { return N() + OFF; } }
  const jf = (d, f) => new D(d.getTime() + J)["getUTC" + f]();
  Object.assign(FD.prototype, { getHours() { return jf(this, "Hours"); }, getMinutes() { return jf(this, "Minutes"); }, getDate() { return jf(this, "Date"); },
    getDay() { return jf(this, "Day"); }, getMonth() { return jf(this, "Month"); }, getFullYear() { return jf(this, "FullYear"); },
    setDate(n) { return this.setTime(this.getTime() + (n - this.getDate()) * 86400000); } });
  window.Date = FD;
  const real = window.fetch.bind(window);
  const load = {};
  const get = f => load[f] || (load[f] = real(f).then(r => r.json()));
  const json = (x, st = 200) => new Response(JSON.stringify(x), { status: st, headers: { "Content-Type": "application/json" } });
  const REPLY = "今の1件は「統計学 演習問題 第5回」です。最初の一歩は、問題 1〜3 を解くことだけ。いま 12:30 に始めれば、次の授業（PSY 14:00）の前に終わります。\n（これはデモの返事です。本物ではここで、予定や課題を実際に直したり、資料を調べたりします）";
  /* 設定の部屋のデモ: 保存も接続も、この画面の中だけ（実際には何も書かない・通信しない） */
  let SET = null;
  const view = () => ({ settings: SET.settings, connections: SET.connections, schema: SET.schema });
  /* 本物と同じ規則（https だけ・このパソコンや社内向けのアドレスは不可）を、デモでも見せる */
  const badUrl = v => { let p; try { p = new URL(String(v || "").trim()); } catch (e) { return "https:// で始まる URL を貼ってください"; }
    const h = p.hostname.toLowerCase();
    if (p.protocol !== "https:") return "https:// で始まる URL を貼ってください";
    if (h === "localhost" || h.endsWith(".local") || h.endsWith(".internal") || /^\[?::1\]?$/.test(h) || /^(127|10)\./.test(h) || /^192\.168\./.test(h) || /^172\.(1[6-9]|2\d|3[01])\./.test(h) || /^169\.254\./.test(h) || h === "0.0.0.0") return "このパソコンや社内向けのアドレスは使えません";
    return ""; };
  async function settingsApi(u, method, opt) {
    if (!SET) SET = JSON.parse(JSON.stringify(await get("demo_settings.json")));
    const body = (() => { try { return JSON.parse(opt.body); } catch (e) { return {}; } })();
    const conn = id => SET.connections.find(c => c.id === id) || {};
    if (u === "/api/settings" && method === "GET") return json(view());
    if (u === "/api/settings_export") return json({ v: 1, ...SET.settings });
    if (u === "/api/settings") { Object.assign(SET.settings, body.set || {}); if (body.set && body.set.hints) SET.settings.hints = { ...SET.settings.hints, ...body.set.hints }; return json(view()); }
    if (u === "/api/settings_reset") { SET.settings = { name: "", day_end: "22:00", max_tasks: 5, tone: "short", hints: {} }; return json(view()); }
    if (u === "/api/secret" && body.id === "moodle" && badUrl(body.value)) return json({ error: badUrl(body.value) }, 400);
    if (u === "/api/secret") { const c = conn(body.id); if (body.id === "moodle") { c.set = true; c.tail = "….ics"; } else { c.client = true; } return json(view()); }
    if (u === "/api/secret_clear") { const c = conn(body.id); c.set = false; c.tail = ""; return json(view()); }
    if (u === "/api/secret_restore") { const c = conn(body.id); c.set = true; if (body.id === "moodle") c.tail = "…ics"; return json(view()); }
    if (u === "/api/google_authorize") { const c = conn("google"); c.auth = { state: "waiting", msg: "ブラウザで「許可」を押してください" }; setTimeout(() => { c.auth = { state: "ok", msg: "許可されました" }; c.set = true; }, 2500); return json(view()); }
    if (u === "/api/connect_test") {
      const id = body.id;
      if (id === "moodle" && body.value !== undefined && badUrl(body.value)) return json({ result: { ok: false, detail: badUrl(body.value), fix: "URL を取り直して貼ってください" }, ...view() });
      const ok = id === "moodle" ? { ok: true, detail: "12件の予定が見つかりました", fix: "" } : id === "ai" ? { ok: true, detail: "見つかりました（Claude Code）", fix: "" }
        : conn("google").set ? { ok: true, detail: "3個のカレンダーが見つかりました", fix: "" } : { ok: false, detail: "まだ許可されていません", fix: "JSON を貼って「許可する」を押してください" };
      return json({ result: ok, ...view() });
    }
    return json(view());
  }
  window.fetch = async function (url, opt) {
    const u = String(url), method = ((opt && opt.method) || "GET").toUpperCase();
    if (u === "/api/state") return json(await get("demo_state.json"));
    if (u === "/api/version") return json({ v: "demo", page: "demo" });
    if (u.startsWith("/api/day?")) { const d = new URLSearchParams(u.split("?")[1]).get("date"); const all = await get("demo_days.json"); const x = all[d] || { date: d, classes: [], allday: [], dues: [], did: [] }; return json(x); }
    if (u.startsWith("/api/note?")) { const id = new URLSearchParams(u.split("?")[1]).get("id"); const n = (await get("demo_notes.json"))[id]; return n ? json(n) : json({ error: "demo" }, 404); }
    if (u === "/api/chat/history") return json({ turns: [{ role: "user", text: "いま何をやればいい？" }, { role: "assistant", text: "「統計学 演習問題 第5回」です。最初の一歩は、問題 1〜3 を解くだけ。" }] });
    if (u.startsWith("/api/dev")) return json({ jobs: [] });
    if (u === "/api/settings" || u === "/api/settings_export" || (method === "POST" && /^\/api\/(secret|secret_clear|secret_restore|settings_reset|connect_test|google_authorize|collect_now)$/.test(u))) return settingsApi(u, method, opt);
    if ((u === "/api/note_create" || u === "/api/note_check") && method === "POST") { let id = ""; try { id = JSON.parse(opt.body).id; } catch (e) {} const n = (await get("demo_notes.json"))[id]; return n ? json(n) : json({ exists: false }); }
    if (u === "/api/chat" && method === "POST") {
      const enc = new TextEncoder();
      const stream = new ReadableStream({ async start(c) { await new Promise(r => setTimeout(r, 1500)); for (const ch of REPLY.match(/.{1,6}/gs)) { if (opt && opt.signal && opt.signal.aborted) { c.error(new DOMException("aborted", "AbortError")); return; } c.enqueue(enc.encode(JSON.stringify({ delta: ch }) + "\n")); await new Promise(r => setTimeout(r, 40)); } c.enqueue(enc.encode(JSON.stringify({ done: REPLY }) + "\n")); c.close(); } });
      return new Response(stream, { status: 200 });
    }
    if (method === "POST") return json(await get("demo_state.json"));   // 操作は見た目だけ（データは変わらない）
    if (u.startsWith("/api/")) return json({ error: "demo" }, 404);   // デモは外へ通信しない
    return real(url, opt);
  };
  /* 説明用: #chat で話す欄に1回話しかける／#note で最初の課題のノートを開く（スクリーンショット用） */
  window.addEventListener("load", () => setTimeout(() => {
    if (location.hash === "#chat") { const b = document.getElementById("chattext"); if (b) { b.value = "いま何をやればいい？"; b.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); } }
    if (location.hash === "#note" && typeof openNote === "function") openNote("demo-1");
    if (location.hash === "#settings") { const g = document.getElementById("gear"); if (g) g.click(); setTimeout(() => { const b = document.querySelector('[data-sopen="google"]'); if (b) b.click(); }, 700); }
  }, 800));
})();
