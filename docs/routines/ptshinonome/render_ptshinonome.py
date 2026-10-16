#!/usr/bin/env python3
"""パークタワー東雲 週次ページのレンダラ

使い方:
  python3 render_ptshinonome.py input.json ptshinonome-template.html out.html next-data.json

input.json の形（1週分。判断を要する文章はタスク側が書き、数値はこのスクリプトが計算する）:
{
 "date": "2026-09-25",                 # 今回の実行日
 "suumo_count": 24,                    # SUUMOページ記載の件数
 "prev": { ...前回の ptshinonome-data-*.json をそのまま... },
 "units": [                            # 今回SUUMOの住戸（名寄せ済み。nc は配列）
   {"nc":["21603969"],"price_man":9880,"sqm":60.15,"dir":"南","plan":"2LDK","floor":null,
    "status":null, "prev_price_man":null, "note":""}
   # status / prev_price_man は通常 null のままでよい（nc 照合で自動判定）。
   # nc が一致しないがノムコム「新価格」等で同一住戸と確認できた改定だけ
   # "status":"改定","prev_price_man":12990 と手で入れる。
   # 同一性が未確認の新規は "note":"既存住戸との関係未確認" のように書く。
 ],
 "off_suumo": [{"price_man":14300,"sqm":72.16,"dir":"南","plan":"2LDK","floor":37,
                "src":"ノムコム/M-STANDARD","note":"SUUMOから消滅、他サイトで掲載継続"}],
 "ended_notes": {"21248437":"成約か取り下げか未確認"},   # 掲載終了に添える注記（nc→文）
 "ignore_ended_nc": ["21603564"],      # 同一住戸の別nc入れ替えなど、掲載終了に数えない nc
 "home_value_man": [11550, 12300],
 "home_status": "据え置き（所有者判断待ち）",
 "summary": ["自動で出ない補足だけ。0〜2件、各40字以内"],
                                       # 新規・価格改定・掲載終了の箇条書きはこのスクリプトが units と
                                       # 前回データから自動で作る（page の changes）。ここに価格を並べない。
 "decisions": [{"tag":"価格改定","text":"..."}],   # tag: 価格改定/新規/掲載終了/北・北西/未確認/その他
 "notes": ["脚注。階数の出所など"],
 "open_issues": ["最大6件、各50字以内"]
}

出力:
 - out.html: テンプレートの __PAGE_DATA__ を置き換えたページ
 - next-data.json: 次回照合用（Drive 保存用、1行JSON）。history に今回分を追記済み
 - 標準出力: 検算結果（件数・中央値など）
"""
import json, statistics, sys

TSUBO = 0.3025
STATION_AVG = 535          # 東雲駅平均（自作指標、万円/坪）
HOME_TSUBO = 19.42         # 自宅 64.19㎡
HOME_SQM = 64.19
NORTH = ("北", "北西")


def tsubo_price(price, sqm):
    return round(price / (sqm * TSUBO), 1)


def main(inp_path, tpl_path, out_path, next_path):
    d = json.load(open(inp_path, encoding="utf-8"))
    prev = d.get("prev") or {}
    prev_units = prev.get("units", [])
    prev_by_nc = {}
    for u in prev_units:
        for nc in u.get("nc", []):
            prev_by_nc[nc] = u

    units = []
    revisions = 0
    cur_nc = set()
    for u in d["units"]:
        u = dict(u)
        cur_nc.update(u["nc"])
        match = next((prev_by_nc[n] for n in u["nc"] if n in prev_by_nc), None)
        if u.get("floor") is None and match and match.get("floor") is not None:
            u["floor"] = match["floor"]
        if not u.get("status"):
            if match is None:
                u["status"] = "新規"
            elif match["price_man"] != u["price_man"]:
                u["status"] = "改定"
                u["prev_price_man"] = match["price_man"]
            else:
                u["status"] = "継続"
        if u["status"] == "改定":
            revisions += 1
        u["tsubo_man"] = tsubo_price(u["price_man"], u["sqm"])
        u.setdefault("note", "")
        units.append(u)
    units.sort(key=lambda x: (x["price_man"], x["sqm"]))

    ignore = set(d.get("ignore_ended_nc", []))
    ended_notes = d.get("ended_notes", {})
    ended = []
    for u in prev_units:
        ncs = set(u.get("nc", []))
        if ncs & cur_nc:
            continue
        if ncs and ncs <= ignore:
            continue
        e = dict(u)
        e["tsubo_man"] = tsubo_price(u["price_man"], u["sqm"])
        e["note"] = next((ended_notes[n] for n in u.get("nc", []) if n in ended_notes), "")
        ended.append(e)

    med = round(statistics.median([u["tsubo_man"] for u in units]), 1)
    ratio = round(med / STATION_AVG, 3)
    metrics = {"suumo_count": d["suumo_count"], "units_count": len(units),
               "tsubo_median_man": med, "ratio_shinonome": ratio,
               "price_revisions": revisions}
    pm = prev.get("metrics", {})

    # 北・北西向きの19.42坪換算
    prev_conv = {}
    for u in prev_units:
        if u["dir"] in NORTH:
            prev_conv[tuple(u["nc"])] = round(tsubo_price(u["price_man"], u["sqm"]) * HOME_TSUBO)
    north = []
    for u in units:
        if u["dir"] in NORTH:
            conv = round(u["tsubo_man"] * HOME_TSUBO)
            pc = None
            for k, v in prev_conv.items():
                if set(k) & set(u["nc"]):
                    pc = v
            north.append({"dir": u["dir"], "plan": u["plan"], "sqm": u["sqm"], "floor": u["floor"],
                          "tsubo_man": u["tsubo_man"], "conv_man": conv,
                          "change_man": None if pc is None else conv - pc,
                          "is_new": pc is None})

    # 今週の変化（概要に1住戸1行で出す。住戸の注記は全住戸表の脚注にだけ出し、ここには載せない）
    kind_rank = {"値下げ": 0, "値上げ": 0, "価格改定": 0, "新規": 1, "掲載終了": 2}
    changes = []
    for u in units:
        if u["status"] == "改定":
            pp = u.get("prev_price_man")
            kind = "価格改定" if pp is None else ("値下げ" if u["price_man"] < pp else "値上げ")
        elif u["status"] == "新規":
            kind, pp = "新規", None
        else:
            continue
        changes.append({"kind": kind, "dir": u["dir"], "plan": u["plan"], "sqm": u["sqm"],
                        "floor": u["floor"], "price_man": u["price_man"], "prev_price_man": pp})
    for e in ended:
        changes.append({"kind": "掲載終了", "dir": e["dir"], "plan": e["plan"], "sqm": e["sqm"],
                        "floor": e.get("floor"), "price_man": e["price_man"], "prev_price_man": None})
    changes.sort(key=lambda c: (kind_rank[c["kind"]], c["price_man"], c["sqm"]))

    history = [h for h in prev.get("history", []) if h["date"] != d["date"]]
    history.append({"date": d["date"], "median": med, "units": len(units),
                    "ratio": ratio, "revisions": revisions})
    history.sort(key=lambda h: h["date"])

    page = {
        "date": d["date"], "prev_date": prev.get("date"),
        "metrics": metrics, "prev_metrics": pm,
        "station_avg": STATION_AVG, "home_sqm": HOME_SQM, "home_tsubo": HOME_TSUBO,
        "home_value_man": d["home_value_man"], "home_status": d.get("home_status", ""),
        "prev_home_value_man": prev.get("home_value_man"),
        "changes": changes,
        "summary": d.get("summary", [])[:3], "decisions": d.get("decisions", []),
        "units": units, "ended": ended, "off_suumo": d.get("off_suumo", []),
        "north": north, "history": history,
        "notes": d.get("notes", []), "open_issues": d.get("open_issues", [])[:6],
    }
    blob = json.dumps(page, ensure_ascii=False).replace("</", "<\\/")
    tpl = open(tpl_path, encoding="utf-8").read()
    if tpl.count("__PAGE_DATA__") != 1:
        sys.exit("template must contain exactly one __PAGE_DATA__ marker")
    open(out_path, "w", encoding="utf-8").write(tpl.replace("__PAGE_DATA__", blob))

    nxt = {
        "date": d["date"],
        "units": [{k: u[k] for k in ("nc", "price_man", "sqm", "dir", "plan", "floor")} for u in units],
        "off_suumo": [{k: o.get(k) for k in ("price_man", "sqm", "dir", "plan", "floor", "src")}
                      for o in d.get("off_suumo", [])],
        "metrics": metrics,
        "home_value_man": d["home_value_man"],
        "open_issues": d.get("open_issues", [])[:6],
        "history": history,
    }
    open(next_path, "w", encoding="utf-8").write(json.dumps(nxt, ensure_ascii=False, separators=(",", ":")))

    print(f"SUUMO件数 {d['suumo_count']} / 名寄せ後 {len(units)}戸 / 坪単価中央値 {med} / 倍率 {ratio} / 改定 {revisions}件")
    print("新規:", [(u["price_man"], u["sqm"], u["dir"], u["plan"]) for u in units if u["status"] == "新規"])
    print("改定:", [(u["prev_price_man"], u["price_man"], u["sqm"], u["dir"], u["plan"]) for u in units if u["status"] == "改定"])
    print("掲載終了:", [(e["price_man"], e["sqm"], e["dir"], e["plan"], e["nc"]) for e in ended])
    print("階数未取得:", sum(1 for u in units if u["floor"] is None), "戸")


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    main(*sys.argv[1:])
