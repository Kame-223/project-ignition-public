"""札の「上端・下端の真ん中に擦り付けると開く」の判定（画面に依存しない計算だけ。テストできるように float.py から分けた）。

判定は「窓が、画面の上端（メニューバーのすぐ下）か下端に、真ん中あたりで触れているか」だけ。指の位置や動かした向きは見ない。
隅に置いたときの窓は、縁から MARGIN(14) 離れているので、触れていることにはならない。"""

EDGE_PT = 8      # 縁にこれ以内まで寄せたら「触れている」（隅の余白 14 より小さい）
MIDDLE = (0.30, 0.70)  # 画面の幅の、ここからここまでの間に窓の中心があるとき（真ん中）


def edge_zone(x, y, w, h, box, full_h):
    """窓の左上 (x, y)・大きさ (w, h)（左上原点）が、上端の真ん中なら "TC"、下端の真ん中なら "BC"、そうでなければ None。
    box = (L, T, W, H)＝メニューバーと Dock を除いた使える範囲。full_h = 画面の全体の高さ（下端用）。"""
    L, T, W, H = box
    cx = x + w / 2
    if not (L + W * MIDDLE[0] <= cx <= L + W * MIDDLE[1]):
        return None
    if y <= T + EDGE_PT:
        return "TC"
    if y + h >= full_h - EDGE_PT:
        return "BC"
    return None
