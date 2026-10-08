# -*- coding: utf-8 -*-
"""
技师与场地排班工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 当日预约汇总（模型承担）  把各渠道预约单整理成结构化待排清单
  S2 排班表生成                本步（脚本承担）确定性排位：
                               指名优先 → 期望时段优先 → 班次/午休/培训闸门
                               → 房间类型匹配 → 15 分钟消毒缓冲
  S3 负荷均衡调整建议          本步（脚本承担）按技师负荷与房间利用率给调整建议
  S4 人工确认                  店长确认排班表后下发

确定性规则（与 prompt.txt 一致）：
  R1 排位顺序  待排单按「指名优先级 → 期望时段 → 提交顺序」排序，逐单安置
  R2 占用闸门  技师班次 / 午休 / 培训段 / 已排单（含 15 分钟缓冲）不可重叠
  R3 房间匹配  项目类型与房间类型一致；美容房仅 1 间，推拿/放松进理疗房
  R4 负荷统计  每位技师：接单数 / 在岗接单时长 / 班次利用率（接单时长÷可接单时长）
               利用率 >85% 标「超负荷」，<30% 标「空闲，可补单」

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/当日排班表.xlsx   排班表 / 负荷统计 / 汇总 三 sheet
  out/技师负荷.png      各技师利用率柱状图
  out/staff_flow.json   机器可读结果
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

BUFFER = 15


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fh(m):
    return f"{m // 60:02d}:{m % 60:02d}"


DEMO = {
    "day": "2026-10-08",
    "close_min": hm("20:00"),
    "last_start_min": hm("19:00"),
    "items": {
        "头部放松30分钟": {"type": "massage", "minutes": 30},
        "小气泡深层清洁": {"type": "care", "minutes": 30},
        "足部推拿45分钟": {"type": "massage", "minutes": 45},
        "精油SPA 60分钟": {"type": "spa", "minutes": 60},
        "肩颈推拿45分钟": {"type": "massage", "minutes": 45},
        "香薰SPA 60分钟": {"type": "spa", "minutes": 60},
    },
    "technicians": {
        "王丽": {"shift": ("10:00", "16:00"), "blocks": []},
        "张曼": {"shift": ("12:00", "20:00"), "blocks": [("14:00", "15:00", "外出培训")]},
        "陈露": {"shift": ("10:00", "20:00"), "blocks": [("12:30", "13:30", "午休")]},
    },
    "rooms": [
        {"name": "美容房", "types": ["spa", "care"]},
        {"name": "理疗房1", "types": ["massage", "care"]},
        {"name": "理疗房2", "types": ["massage", "care"]},
    ],
    "bookings": [
        {"id": 1, "customer": "王馨", "item": "头部放松30分钟", "technician": None, "earliest": "10:00"},
        {"id": 2, "customer": "何琳", "item": "小气泡深层清洁", "technician": None, "earliest": "10:30"},
        {"id": 3, "customer": "郭艳", "item": "足部推拿45分钟", "technician": "陈露", "earliest": "11:00"},
        {"id": 4, "customer": "韩雪", "item": "精油SPA 60分钟", "technician": "王丽", "earliest": "13:00"},
        {"id": 5, "customer": "白露", "item": "肩颈推拿45分钟", "technician": None, "earliest": "13:30"},
        {"id": 6, "customer": "江月", "item": "香薰SPA 60分钟", "technician": "王丽", "earliest": "14:30"},
        {"id": 7, "customer": "沈青", "item": "足部推拿45分钟", "technician": None, "earliest": "15:30"},
        {"id": 8, "customer": "唐果", "item": "小气泡深层清洁", "technician": None, "earliest": "16:00"},
    ],
}


def free_slots(payload, tech_name, svc_minutes, earliest_min):
    """技师在 earliest 之后的全部空闲起点（5 分钟粒度）。"""
    cfg = payload["technicians"][tech_name]
    s0, s1 = hm(cfg["shift"][0]), hm(cfg["shift"][1])
    blocks = [(hm(b[0]), hm(b[1])) for b in cfg["blocks"]]
    busy = []
    for b in payload.get("assigned", []):
        if b["technician"] != tech_name:
            continue
        m = payload["items"][b["item"]]["minutes"]
        st = hm(b["start"])
        busy.append((st, st + m + BUFFER))
    starts, t = [], max(s0, earliest_min)
    while t + svc_minutes <= min(s1, payload["close_min"]):
        if t + svc_minutes <= payload["last_start_min"] + svc_minutes or True:
            ok = True
            for bs, be in blocks:
                if t < be and t + svc_minutes > bs:
                    ok = False
                    break
            if ok:
                for os_, oe in busy:
                    if not (t + svc_minutes + BUFFER <= os_ or t >= oe):
                        ok = False
                        break
            if ok and t <= payload["last_start_min"]:
                starts.append(t)
        t += 5
    return starts


def assign(payload):
    """逐单排位：指名技师优先；不限技师时选「能最早开工且利用率最低」的技师。"""
    unsched = []
    for bk in sorted(payload["bookings"], key=lambda x: (0 if x.get("technician") else 1, hm(x["earliest"]), x["id"])):
        svc = payload["items"][bk["item"]]
        earliest = hm(bk["earliest"])
        cands = [bk["technician"]] if bk.get("technician") else list(payload["technicians"])
        best = None
        for name in cands:
            if name not in payload["technicians"]:
                continue
            slots = free_slots(payload, name, svc["minutes"], earliest)
            if not slots:
                continue
            load = sum(payload["items"][b["item"]]["minutes"]
                       for b in payload.get("assigned", []) if b["technician"] == name)
            key = (slots[0], load)
            if best is None or key < best[0]:
                best = (key, name, slots[0])
        if best is None:
            unsched.append({**bk, "原因": "指名技师班次内无可约档期或房间类型不匹配，需改期/改派"})
            continue
        _, name, t = best
        room = next((r["name"] for r in payload["rooms"] if svc["type"] in r["types"]), "待人工分配")
        payload.setdefault("assigned", []).append({
            "customer": bk["customer"], "item": bk["item"], "start": fh(t),
            "end": fh(t + svc["minutes"]), "technician": name, "room": room,
        })
    return payload.get("assigned", []), unsched


def load_stats(payload, assigned):
    stats = []
    for name, cfg in payload["technicians"].items():
        s0, s1 = hm(cfg["shift"][0]), hm(cfg["shift"][1])
        blocks_min = sum(hm(b[1]) - hm(b[0]) for b in cfg["blocks"])
        available = (s1 - s0 - blocks_min - BUFFER)  # 末段预留一次缓冲
        jobs = [b for b in assigned if b["technician"] == name]
        work = sum(payload["items"][b["item"]]["minutes"] for b in jobs)
        util = work / available * 100 if available > 0 else 0
        stats.append({
            "技师": name, "班次": f"{cfg['shift'][0]}-{cfg['shift'][1]}",
            "接单数": len(jobs), "接单时长(分钟)": work,
            "可接单时长(分钟)": available, "利用率": f"{util:.0f}%",
            "状态": "超负荷" if util > 85 else ("空闲，可补单" if util < 30 else "正常"),
        })
    return stats


def build(payload, outdir):
    if not payload.get("bookings"):
        raise SystemExit("[错误] bookings 为空：无待排单不排班，请先汇总当日预约")
    assigned, unsched = assign(payload)
    assigned.sort(key=lambda b: b["start"])
    stats = load_stats(payload, assigned)
    total_load = sum(s["接单数"] for s in stats)
    summary = [
        {"项": "日期", "内容": payload.get("day", "")},
        {"项": "待排单数", "内容": f"{len(payload['bookings'])} 单"},
        {"项": "已排定", "内容": f"{len(assigned)} 单"},
        {"项": "未能排入", "内容": f"{len(unsched)} 单（原因见排班表）"},
        {"项": "技师接单合计", "内容": f"{total_load} 单"},
        {"项": "排位规则", "内容": "指名优先 → 期望时段优先；每单后留 15 分钟消毒缓冲"},
        {"项": "合规提示", "内容": "AI 生成内容；排班表经店长确认后下发"},
    ]
    rows = [{**b, "状态": "已排定"} for b in assigned] + \
           [{**u, "start": "—", "end": "—", "technician": "—", "room": "—", "状态": "未能排入"}
            for u in unsched]

    at.ensure_outdir(outdir)
    xlsx = at.write_excel(
        os.path.join(outdir, "当日排班表.xlsx"),
        {"排班表": rows, "负荷统计": stats, "汇总": summary},
        highlights={"排班表": {"状态": "contains:未能排入"},
                    "负荷统计": {"状态": "contains:超负荷"}},
        widths={"排班表": {"customer": 10, "item": 18}},
    )
    png = at.bar_chart(
        os.path.join(outdir, "技师负荷.png"),
        [s["技师"] for s in stats],
        [float(s["利用率"].rstrip("%")) for s in stats],
        title="技师班次利用率（>85% 超负荷 / <30% 可补单）", ylabel="利用率 %",
    )
    js = at.write_json({"assigned": assigned, "unscheduled": unsched, "load": stats,
                        "summary": {r["项"]: r["内容"] for r in summary},
                        "generated_at": at.stamp(),
                        "note": "排位与负荷由脚本确定性计算；改派沟通与下发确认由模型+人工完成"},
                       os.path.join(outdir, "staff_flow.json"))
    print(f"排班完成 —— 已排 {len(assigned)}/{len(payload['bookings'])} 单，"
          f"未排 {len(unsched)} 单")
    for f in (xlsx, png, js):
        print(" 产物:", f)
    at.emit({"status": "ok", "files": [xlsx, png, js], "assigned": len(assigned)})


def main():
    ap = argparse.ArgumentParser(description="技师与场地排班工作流 —— 确定性排位与负荷统计")
    ap.add_argument("--input", help="输入 JSON（items/technicians/rooms/bookings）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    payload = DEMO if a.demo else (at.read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(payload, a.outdir)


if __name__ == "__main__":
    main()
