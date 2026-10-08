#!/usr/local/bin/python3
"""適用のあと、全体画面（と札）を再起動し、起動を確かめる。確かめられなければ、適用を自動で元に戻して再起動し直す。
使い方: dev_restart.py <依頼の id> <適用前のコミット（空なら戻すだけ）> <id> <変えたファイル（カンマ区切り）>
"""
import os
import pathlib
import subprocess
import sys
import time
import urllib.request

BASE = pathlib.Path(__file__).resolve().parent.parent
SYS_PY = "/usr/local/bin/python3"
URL = "http://127.0.0.1:8774/"


def healthy(tries=20):
    """起動の確認: トップの HTML だけでなく、状態の API が正しい形で返ること（状態が壊れた適用を、成功にしない）。"""
    import json
    for _ in range(tries):
        try:
            with urllib.request.urlopen(URL, timeout=3) as r:
                ok_top = r.status == 200
                sys.path.insert(0, str(BASE / "bin"))
                import jscheck
                js_ok, _why = jscheck.check_html(r.read().decode("utf-8", "replace"))
                if js_ok is False:  # 画面の JavaScript が壊れている（API は動いていても、画面が使えない）
                    ok_top = False
            with urllib.request.urlopen(URL + "api/state", timeout=8) as r2:
                st = json.loads(r2.read())
                if ok_top and r2.status == 200 and "plan" in st and "tasks" in st:
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def restart():
    subprocess.run(["pkill", "-f", "bin/main_page.py"], check=False)
    time.sleep(1)
    subprocess.Popen([SYS_PY, str(BASE / "bin" / "main_page.py")], cwd=str(BASE), start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    jid, before, _id, files = sys.argv[1:5]
    time.sleep(2)  # 返事を返し終えるのを待つ
    restart()
    if before and not healthy():  # 起動しない → 適用を元に戻す
        sys.path.insert(0, str(BASE / "bin"))
        import dev_agent
        try:
            dev_agent.revert(jid, restart=False)
            dev_agent._update(jid, status="reverted", note="適用後に起動しなかったので、自動で元に戻しました")
        except Exception as e:
            dev_agent._update(jid, note=f"自動で戻せませんでした: {e}")
        restart()
    if any(f.startswith("bin/float") or f == "bin/core.py" for f in files.split(",")):
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.example.float"], check=False)


if __name__ == "__main__":
    main()
