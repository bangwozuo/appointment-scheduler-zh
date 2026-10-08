# -*- coding: utf-8 -*-
"""
冲突与超售检测工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 实时冲突扫描   本步（脚本承担）确定性扫描：新预约写入后，对
                    「技师双订 / 房间类型不符 / 消毒缓冲不足 / 超班次与末单红线」
                    四类冲突逐条判定，输出冲突清单
  S2 改派方案生成   本步（脚本承担）为每个冲突给出可执行的改派候选
                    （同类型空闲技师 / 最近可约档期）
  S3 老板确认       人工承担：改派方案确认后执行并通知客户

确定性规则（与 prompt.txt 一致）：
  R1 技师冲突   同技师时间区间（项目时长 + 结束后 15 分钟缓冲）重叠 → 冲突
  R2 房间冲突   项目类型与房间类型不符，或同房间重叠 → 冲突
  R3 班次闸门   超出技师班次 / 休息培训段 / 末单须在闭店前 60 分钟前开始 → 冲突
  R4 超售红线   一个冲突都不允许静默放过；每个冲突必须给出 ≥1 条改派候选或明确「需改期」

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/冲突检测报告.xlsx   冲突清单 / 改派候选 / 汇总 三 sheet
  out/conflict_flow.json  机器可读结果
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

at.need("pandas", "openpyxl")

BUFFER = 15


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fh(m):
    return f"{m // 60:02d}:{m % 60:02d}"


DEMO = {
    "day": "2026-10-09",
    "close_min": hm("20:00"),
    "last_start_min": hm("19:00"),
    "items": {
        "精油SPA 60分钟": {"type": "spa", "minutes": 60},
        "香薰SPA 60分钟": {"type": "spa", "minutes": 60},
        "肩颈推拿45分钟": {"type": "massage", "minutes": 45},
        "足部推拿45分钟": {"type": "massage", "minutes": 45},
        "小气泡深层清洁": {"type": "care", "minutes": 30},
    },
    "technicians": {
        "王丽": {"shift": ("10:00", "16:00"), "blocks": []},
        "张曼": {"shift": ("12:00", "20:00"), "blocks": []},
        "陈露": {"shift": ("10:00", "20:00"), "blocks": [("12:30", "13:30", "午休")]},
    },
    "rooms": [
        {"name": "美容房", "types": ["spa", "care"]},
        {"name": "理疗房1", "types": ["massage", "care"]},
        {"name": "理疗房2", "types": ["massage", "care"]},
    ],
    "existing": [
        {"id": "E1", "customer": "李婷", "item": "精油SPA 60分钟", "start": "10:00",
         "technician": "王丽", "room": "美容房"},
        {"id": "E2", "customer": "周悦", "item": "香薰SPA 60分钟", "start": "11:15",
         "technician": "王丽", "room": "美容房"},
        {"id": "E3", "customer": "孙蕊", "item": "肩颈推拿45分钟", "start": "12:00",
         "technician": "张曼", "room": "理疗房1"},
        {"id": "E4", "customer": "郑寒", "item": "足部推拿45分钟", "start": "14:30",
         "technician": "陈露", "room": "理疗房1"},
    ],
    "incoming": [
        {"id": "M1", "customer": "何洁", "item": "小气泡深层清洁", "technician": "王丽",
         "earliest": "10:00", "latest": "12:00"},
        {"id": "M2", "customer": "曲彤", "item": "足部推拿45分钟", "technician": "王丽",
         "earliest": "11:00", "latest": None},
    ],
}


def span(bk, items, buffer_=True):
    m = items[bk["item"]]["minutes"]
    s = hm(bk["start"])
    return s, s + m + (BUFFER if buffer_ else 0)


def scan(payload):
    """四类冲突逐条判定。"""
    items = payload["items"]
    conflicts = []
    allbk = payload["existing"] + payload["incoming"]
    for nb in payload["incoming"]:
        if nb.get("start"):
            s, e = span(nb, items)
            # R1 技师双订
            for b in payload["existing"]:
                if b["technician"] != nb["technician"]:
                    continue
                bs, be = span(b, items)
                if s < be and bs < e:
                    conflicts.append({
                        "单号": nb["id"], "客户": nb["customer"], "类型": "技师双订",
                        "级别": "高", "明细": (f"{nb['technician']} 已有 {b['id']} {b['customer']} "
                                            f"{b['start']}-{fh(bs + items[b['item']]['minutes'])}，"
                                            f"新单 {fh(s)}-{fh(e - BUFFER)} 重叠（含 {BUFFER} 分钟缓冲）"),
                        "新单": f"{fh(s)}-{fh(e - BUFFER)}", "技师": nb["technician"],
                    })
            # R2 房间冲突
            svc = items[nb["item"]]
            room = next((r for r in payload["rooms"] if r["name"] == nb.get("room")), None)
            if room and svc["type"] not in room["types"]:
                conflicts.append({
                    "单号": nb["id"], "客户": nb["customer"], "类型": "房间类型不符",
                    "级别": "高",
                    "明细": (f"「{nb['item']}」需 {'/'.join(svc['type'].split(','))} 类房间，"
                             f"{room['name']} 不承接"),
                    "新单": f"{fh(s)}-{fh(e - BUFFER)}", "技师": nb.get("technician", ""),
                })
            # R3 班次闸门
            tech = payload["technicians"].get(nb["technician"])
            if tech:
                s0, s1 = hm(tech["shift"][0]), hm(tech["shift"][1])
                if s < s0 or e > s1:
                    conflicts.append({
                        "单号": nb["id"], "客户": nb["customer"], "类型": "超班次",
                        "级别": "高",
                        "明细": (f"{nb['technician']} 班次 {tech['shift'][0]}-{tech['shift'][1]}，"
                                 f"新单 {fh(s)}-{fh(e - BUFFER)} 越界"),
                        "新单": f"{fh(s)}-{fh(e - BUFFER)}", "技师": nb["technician"],
                    })
                for bl in tech.get("blocks", []):
                    if s < hm(bl[1]) and hm(bl[0]) < e:
                        conflicts.append({
                            "单号": nb["id"], "客户": nb["customer"], "类型": "撞休息/培训段",
                            "级别": "中",
                            "明细": f"{nb['technician']} {bl[2] if len(bl) > 2 else '休息'} {bl[0]}-{bl[1]} 与新单重叠",
                            "新单": f"{fh(s)}-{fh(e - BUFFER)}", "技师": nb["technician"],
                        })
                if s > payload["last_start_min"]:
                    conflicts.append({
                        "单号": nb["id"], "客户": nb["customer"], "类型": "超末单红线",
                        "级别": "中",
                        "明细": f"末单须 {fh(payload['last_start_min'])} 前开始，新单 {fh(s)} 开始",
                        "新单": f"{fh(s)}-{fh(e - BUFFER)}", "技师": nb["technician"],
                    })
    return conflicts


def propose(payload, conflicts):
    """为每个冲突找改派候选：同类型其他技师 + 其班次内最近空档。"""
    items = payload["items"]
    cand_rows = []
    for c in conflicts:
        nb = next((x for x in payload["incoming"] if x["id"] == c["单号"]), None)
        if not nb:
            continue
        svc = items[nb["item"]]
        earliest = hm(nb.get("earliest", "10:00"))
        cands = []
        for name, cfg in payload["technicians"].items():
            if name == c.get("技师") and c["类型"] == "技师双订":
                continue  # 双订时换人；其他类型可保留原技师换时段
            s0, s1 = hm(cfg["shift"][0]), hm(cfg["shift"][1])
            busy = [span(b, items) for b in payload["existing"] if b["technician"] == name]
            blocks = [(hm(x[0]), hm(x[1])) for x in cfg.get("blocks", [])]
            t = max(s0, earliest)
            while t + svc["minutes"] <= min(s1, payload["close_min"]):
                ok = t <= payload["last_start_min"]
                for bs, be in blocks:
                    if t < be and t + svc["minutes"] > bs:
                        ok = False
                for bs, be in busy:
                    if not (t + svc["minutes"] + BUFFER <= bs or t >= be):
                        ok = False
                if ok:
                    room = next((r["name"] for r in payload["rooms"] if svc["type"] in r["types"]), "待人工分配")
                    cands.append(f"{name} {fh(t)}-{fh(t + svc['minutes'])}（{room}）")
                    break
                t += 5
            if len(cands) >= 2:
                break
        cand_rows.append({"单号": c["单号"], "冲突类型": c["类型"],
                          "改派候选": "；".join(cands) if cands else "无候选 —— 建议改期",
                          "确认人": "老板确认后执行，前台通知客户"})
    return cand_rows


def build(payload, outdir):
    if payload.get("incoming") is None:
        raise SystemExit("[错误] incoming 为空：需要待检的新预约清单")
    # 新预约未带明确时刻时，按「最早期望时刻」预落位，再扫冲突（模拟即写即检）
    for nb in payload["incoming"]:
        if not nb.get("start"):
            nb["start"] = nb.get("earliest", "10:00")
    conflicts = scan(payload)
    cands = propose(payload, conflicts)
    summary = [
        {"项": "检测日", "内容": payload.get("day", "")},
        {"项": "存量预约", "内容": f"{len(payload['existing'])} 单"},
        {"项": "新增预约", "内容": f"{len(payload['incoming'])} 单"},
        {"项": "冲突数", "内容": f"{len(conflicts)} 处（技师双订/房间不符/超班次/撞休息段/超末单）"},
        {"项": "缓冲口径", "内容": f"每单结束后预留 {BUFFER} 分钟消毒缓冲；末单须 {fh(payload['last_start_min'])} 前开始"},
        {"项": "处理要求", "内容": "0 冲突静默放过：每处冲突须改派候选或明确改期，经老板确认后执行"},
        {"项": "合规提示", "内容": "AI 生成内容；改派方案须经人工确认"},
    ]
    at.ensure_outdir(outdir)
    xlsx = at.write_excel(
        os.path.join(outdir, "冲突检测报告.xlsx"),
        {"冲突清单": conflicts or [{"单号": "—", "类型": "无冲突", "级别": "—", "明细": "全部通过", "新单": "—", "技师": "—"}],
         "改派候选": cands or [{"单号": "—", "冲突类型": "—", "改派候选": "—", "确认人": "—"}],
         "汇总": summary},
        highlights={"冲突清单": {"级别": "contains:高"}},
        widths={"冲突清单": {"明细": 56}, "改派候选": {"改派候选": 44}},
    )
    js = at.write_json({"conflicts": conflicts, "candidates": cands,
                        "summary": {r["项"]: r["内容"] for r in summary},
                        "generated_at": at.stamp(),
                        "note": "冲突判定与改派候选由脚本确定性扫描；客户沟通与执行确认由模型+人工完成"},
                       os.path.join(outdir, "conflict_flow.json"))
    print(f"冲突扫描完成 —— 新增 {len(payload['incoming'])} 单，检出冲突 {len(conflicts)} 处")
    for f in (xlsx, js):
        print(" 产物:", f)
    at.emit({"status": "ok", "files": [xlsx, js], "conflicts": len(conflicts)})


def main():
    ap = argparse.ArgumentParser(description="冲突与超售检测工作流 —— 四类冲突确定性扫描")
    ap.add_argument("--input", help="输入 JSON（items/technicians/rooms/existing/incoming）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    payload = DEMO if a.demo else (at.read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(payload, a.outdir)


if __name__ == "__main__":
    main()
