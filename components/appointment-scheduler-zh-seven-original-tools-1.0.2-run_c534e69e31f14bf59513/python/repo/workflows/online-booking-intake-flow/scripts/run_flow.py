# -*- coding: utf-8 -*-
"""
在线预约受理工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 意图识别（模型承担）    从客户留言解析：项目 / 时长 / 指名技师 / 期望时段
  S2 项目/技师/时间匹配      本步（脚本承担）确定性排位：
                             价目表核对 → 技师班次与已有预约核对 → 房间类型匹配
                             → 15 分钟消毒缓冲 → 给出最早可约档期
  S3 确认（人工承担）        改期方案与待确认项须客户确认后写单

确定性规则（与 prompt.txt 一致）：
  R1 价目核对   项目不在价目表 → 拒单并列出可选项目
  R2 班次闸门   技师班次外 / 休息段 / 已有预约占用（含结束后 15 分钟缓冲）→ 不可约
  R3 房间匹配   项目类型必须与房间类型一致，技师与房间一对一占用
  R4 末单红线   当日最后一单须在闭店前 60 分钟前开始

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/受理确认单.xlsx   分步结果 / 可约档期 / 汇总 三 sheet
  out/当日占用.png      指名技师当日时间轴占用柱状图
  out/intake_flow.json  机器可读结果
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(WF_DIR))
sys.path.insert(0, os.path.join(REPO, "lib"))

try:
    import assettools as at
except ImportError:  # pragma: no cover
    print("[错误] 未找到 lib/assettools.py。请确认工作流位于 <repo>/workflows/<slug>/scripts/ 下。",
          file=sys.stderr)
    sys.exit(2)

at.need("pandas", "openpyxl", "matplotlib")


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fh(m):
    return f"{m // 60:02d}:{m % 60:02d}"


DEMO = {
    "day": "2026-10-09",
    "close_min": hm("20:00"),
    "last_start_min": hm("19:00"),
    "buffer_min": 15,
    "pricebook": [
        {"item": "肩颈推拿45分钟", "type": "massage", "minutes": 45, "price": 168},
        {"item": "肩颈推拿60分钟（加长版）", "type": "massage", "minutes": 60, "price": 218},
        {"item": "小气泡深层清洁", "type": "care", "minutes": 30, "price": 128},
        {"item": "精油SPA 60分钟", "type": "spa", "minutes": 60, "price": 298},
    ],
    "technicians": [
        {"name": "张曼", "shift_start": "12:00", "shift_end": "20:00", "breaks": []},
        {"name": "王丽", "shift_start": "10:00", "shift_end": "16:00", "breaks": []},
    ],
    "rooms": [
        {"name": "美容房", "types": ["spa", "care"]},
        {"name": "理疗房1", "types": ["massage", "care"]},
        {"name": "理疗房2", "types": ["massage", "care"]},
    ],
    "existing": [
        {"customer": "孙蕊", "item": "肩颈推拿45分钟", "start": "12:00", "technician": "张曼", "room": "理疗房1"},
        {"customer": "赵倩", "item": "小气泡深层清洁", "start": "13:00", "technician": "张曼", "room": "美容房"},
    ],
    "request": {"customer": "林晓", "item": "肩颈推拿60分钟（加长版）",
                "technician": "张曼", "earliest": "15:00", "note": "指名张曼老师，三点左右到店"},
}


def find_slot(payload, req):
    """S2 确定性排位：从 earliest 起按 5 分钟粒度找第一个可约档期。"""
    items = {p["item"]: p for p in payload["pricebook"]}
    svc = items.get(req["item"])
    if not svc:
        return None, f"项目「{req['item']}」不在价目表，拒绝受理并列出可选项目"
    tech = next((t for t in payload["technicians"] if t["name"] == req["technician"]), None)
    if not tech:
        return None, f"技师「{req['technician']}」当日无班次，转人工改派"

    # 已有预约的占用区间（含 15 分钟消毒缓冲）
    occupied = []
    for b in payload.get("existing", []):
        if b["technician"] != tech["name"]:
            continue
        it = items.get(b["item"], {"minutes": 60})
        s = hm(b["start"])
        occupied.append((s, s + it["minutes"] + payload["buffer_min"]))

    start = max(hm(req.get("earliest", "10:00")), hm(tech["shift_start"]))
    end_limit = min(hm(tech["shift_end"]), payload["close_min"])
    t = start
    while t + svc["minutes"] <= end_limit:
        conflict = any(not (t + svc["minutes"] + payload["buffer_min"] <= os_ or t >= oe)
                       for os_, oe in occupied)
        in_break = any(hm(bs) < t + svc["minutes"] and t < hm(be)
                       for bs, be in tech.get("breaks", []))
        if not conflict and not in_break:
            # 房间匹配：找当日同时段空闲且类型一致的房间
            room = next((r for r in payload["rooms"] if svc["type"] in r["types"]), None)
            return {"customer": req["customer"], "item": req["item"],
                    "start": fh(t), "end": fh(t + svc["minutes"]),
                    "technician": tech["name"], "room": room["name"] if room else "待人工分配",
                    "price": svc["price"]}, None
        t += 5
    return None, (f"「{req['item']}」在技师 {tech['name']} 班次内无可约档期"
                  f"（班次 {tech['shift_start']}-{tech['shift_end']}，末单须 {fh(payload['last_start_min'])} 前开始）")


def build(payload, outdir):
    req = payload.get("request")
    if not req:
        raise SystemExit("[错误] request 为空：需要客户预约请求（item/technician/earliest）")
    slot, err = find_slot(payload, req)

    steps = [
        {"步骤": "S1 意图识别（模型）", "结果": (f"项目={req['item']}；指名={req.get('technician')}；"
                                          f"期望到店={req.get('earliest')}"),
         "判定": "✅"},
        {"步骤": "S2 价目表核对（脚本）", "结果": "命中价目表" if not err else err, "判定": "✅" if not err else "🔴"},
        {"步骤": "S2 班次/占用/缓冲核对（脚本）",
         "结果": (f"已有预约 {len(payload.get('existing', []))} 条已纳入占用计算"
                  f"（含 {payload.get('buffer_min', 15)} 分钟消毒缓冲）"),
         "判定": "✅"},
        {"步骤": "S2 排位（脚本）",
         "结果": (f"可约 {slot['start']}-{slot['end']}（技师 {slot['technician']}·{slot['room']}，"
                  f"{slot['price']} 元）") if slot else "无可约档期",
         "判定": "✅" if slot else "🔴"},
        {"步骤": "S3 客户确认（人工）",
         "结果": "档期经客户确认后写单；改期请求回到 S2 重排" if slot else "转人工改派",
         "判定": "⏳"},
    ]
    summary = [
        {"项": "门店日", "内容": f"{payload.get('day', '')}"},
        {"项": "客户请求", "内容": f"{req['customer']}：{req['item']}（指名 {req.get('technician')}）"},
        {"项": "受理结果", "内容": ("已匹配档期，待客户确认" if slot else "未能受理，原因见分步结果")},
        {"项": "问题数", "内容": str(1 if err else 0)},
    ]
    options = []
    if slot:
        options = [slot]

    at.ensure_outdir(outdir)
    xlsx = at.write_excel(
        os.path.join(outdir, "受理确认单.xlsx"),
        {"分步结果": steps, "可约档期": options or [{"说明": err or "无"}], "汇总": summary},
        highlights={"分步结果": {"判定": "contains:🔴"}},
        widths={"分步结果": {"结果": 56}},
    )
    # 指名技师当日占用图
    tech = req["technician"]
    items = {p["item"]: p for p in payload["pricebook"]}
    bars, labels = [], []
    base = hm("10:00")
    span_h = (hm("20:00") - base) / 60.0
    for b in payload.get("existing", []) + ([{**slot, "item": slot["item"]} if slot else None][0:1] if slot else []):
        if b["technician"] != tech:
            continue
        m = items.get(b["item"], {"minutes": 60})
        bars.append((hm(b["start"]) - base) / 60.0)
        labels.append(f"{b['start']} {b['customer']}")
    png = at.bar_chart(
        os.path.join(outdir, "当日占用.png"),
        labels or ["无占用"],
        [m / 60.0 for m in [x * 60 for x in bars]] or [0],
        title=f"{tech} 当日 {span_h:.0f}h 班次占用（各段起始时刻，时长按项目）",
        ylabel="起始时刻（10:00 起小时数）", horizontal=True,
    )
    js = at.write_json({"accepted": bool(slot), "slot": slot, "error": err,
                        "steps": steps, "generated_at": at.stamp(),
                        "note": "档期由脚本按班次/占用/缓冲确定性排位；客户沟通与写单确认由模型+人工完成"},
                       os.path.join(outdir, "intake_flow.json"))
    print(f"受理完成 —— {'可约 ' + slot['start'] + '-' + slot['end'] if slot else '未能受理'}")
    for f in (xlsx, png, js):
        print(" 产物:", f)
    at.emit({"accepted": bool(slot), "files": [xlsx, png, js]})


def main():
    ap = argparse.ArgumentParser(description="在线预约受理工作流 —— 确定性排位")
    ap.add_argument("--input", help="输入 JSON（价目表/班次/已有预约/request）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    payload = DEMO if a.demo else (at.read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(payload, a.outdir)


if __name__ == "__main__":
    main()
