# -*- coding: utf-8 -*-
"""
爽约预测与补位工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 客流需求预测（原子技能承担）  未来 7 天逐日需求预测（另有 demand_forecast.py）
  S2 爽约风险打分                  本步（脚本承担）确定性打分：按已验证特征
                                   （预付状态 / 是否指名 / 时段）给每单打风险分并分级
  S3 高风险预约提前确认            候补触达排期（脚本产出触发时刻，模型写文案）
  S4 爽约后候补补位                爽约确认后按候补名单顺序逐位触达（人工发送）

确定性规则（与 prompt.txt 一致）：
  R1 特征基准   门店历史爽约率基线 7.7%；未预付 12.3% / 有预付 3.1%；
                不指名 9.8% / 指名 5.2%；工作日午间 12:00-14:00 爽约占比 52%
  R2 风险分     risk = 基线 × (预付系数 × 指名系数 × 时段系数)，系数由历史比值定标：
                预付系数 = 未预付率 ÷ 基线；指名系数 = 不指名率 ÷ 基线；
                午间系数 = 1.3，其余时段 1.0（各系数上限 2.0，防止单因子爆表）
  R3 分级       risk ≥ 12% 高风险（提前 2 小时二次确认）/ 8%-12% 中（到店前 30 分钟提醒）
                / < 8% 低（常规提醒）
  R4 补位顺序   候补名单按「可到店时间匹配 → 历史到店率」排序，一位爽约只通知一位候补，
                30 分钟未确认再顺延下一位

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/爽约风险清单.xlsx   风险清单 / 补位排期 / 汇总 三 sheet
  out/爽约风险.png        各单风险分柱状图
  out/noshow_flow.json    机器可读结果
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

BASELINE = 0.077          # 门店 8 周历史爽约率基线
UNPAID_RATE = 0.123       # 未预付订单历史爽约率
PREPAID_RATE = 0.031      # 有预付订单历史爽约率
UNNAMED_RATE = 0.098      # 不指名订单历史爽约率
NAMED_RATE = 0.052        # 指名订单历史爽约率
MIDDAY = ("12:00", "14:00")
MIDDAY_K = 1.3            # 午间时段系数（该时段爽约占全日 52%）
K_CAP = 2.0               # 单因子系数上限


def hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def fh(m):
    return f"{m // 60:02d}:{m % 60:02d}"


DEMO = {
    "day": "2026-10-08",
    "baseline_pct": 7.7,
    "bookings": [
        {"id": 1, "time": "18:00", "customer": "陈琳", "item": "足部推拿45分钟",
         "named": False, "prepay": "none"},
        {"id": 2, "time": "18:30", "customer": "何静", "item": "头部放松30分钟",
         "named": False, "prepay": "deposit"},
        {"id": 3, "time": "19:00", "customer": "郭子涵", "item": "肩颈推拿45分钟",
         "named": False, "prepay": "none"},
        {"id": 4, "time": "19:30", "customer": "孙悦", "item": "香薰SPA 60分钟",
         "named": True, "prepay": "full"},
        {"id": 5, "time": "20:00", "customer": "白婷", "item": "小气泡深层清洁",
         "named": False, "prepay": "none"},
    ],
    "waitlist": [
        {"name": "宋佳", "available_from": "18:30", "channel": "微信"},
        {"name": "袁媛", "available_from": "19:00", "channel": "微信"},
    ],
    "quiet_window": ("21:30", "09:00"),
    "daily_cap": 2,
}


def score(payload, bk):
    base = float(payload.get("baseline_pct", BASELINE * 100)) / 100.0
    prepay = bk.get("prepay", "none")
    if prepay == "none":
        k_prepay = min(K_CAP, UNPAID_RATE / base)
    elif prepay == "deposit":
        # 已付定金介于全款与未付之间：取两者系数的几何中项
        k_prepay = min(K_CAP, (UNPAID_RATE / base * PREPAID_RATE / base) ** 0.5)
    else:  # full 全款
        k_prepay = min(K_CAP, PREPAID_RATE / base)
    k_named = min(K_CAP, UNNAMED_RATE / base) if not bk.get("named") else min(K_CAP, NAMED_RATE / base)
    t = bk.get("time", "12:00")
    k_slot = MIDDAY_K if hm(MIDDAY[0]) <= hm(t) < hm(MIDDAY[1]) else 1.0
    risk = base * k_prepay * k_named * k_slot
    level = "高" if risk >= 0.12 else ("中" if risk >= 0.08 else "低")
    return {
        "单号": bk["id"], "到店时间": bk.get("time", ""), "客户": bk.get("customer", ""),
        "项目": bk.get("item", ""), "预付状态": {"none": "未预付", "deposit": "已付定金",
                                               "full": "已付全款"}[bk.get("prepay", "none")],
        "是否指名": "指名" if bk.get("named") else "不指名",
        "风险分": f"{risk * 100:.1f}%",
        "预付系数": f"×{k_prepay:.2f}", "指名系数": f"×{k_named:.2f}", "时段系数": f"×{k_slot:.1f}",
        "级别": level,
        "建议动作": ("提前 2 小时短信二次确认 + 候补待命" if level == "高"
                    else "到店前 30 分钟提醒" if level == "中" else "常规提醒"),
    }


def build(payload, outdir):
    if not payload.get("bookings"):
        raise SystemExit("[错误] bookings 为空：无待执行预约不评估，请先导出当日预约清单")
    rows = [score(payload, bk) for bk in payload["bookings"]]
    rows.sort(key=lambda r: {"高": 0, "中": 1, "低": 2}[r["级别"]])
    high = [r for r in rows if r["级别"] == "高"]

    # 补位排期：高风险单预排候补触达（触发时刻 = 预约前 2 小时；夜间静默顺延）
    backfill = []
    for r in high:
        t = hm(r["到店时间"]) - 120
        if hm(payload["quiet_window"][0]) < t or t < hm(payload["quiet_window"][1]):
            t = max(t, hm("09:00"))
        for w in payload.get("waitlist", []):
            if hm(w["available_from"]) <= hm(r["到店时间"]):
                backfill.append({
                    "触发时刻": fh(t), "风险单": f"{r['客户']} {r['到店时间']} {r['项目']}",
                    "候补客户": w["name"], "可到店": w["available_from"], "渠道": w["channel"],
                    "状态": "待人工确认发送",
                })
                break
        else:
            backfill.append({"触发时刻": fh(t), "风险单": f"{r['客户']} {r['到店时间']} {r['项目']}",
                             "候补客户": "候补名单无匹配", "可到店": "—", "渠道": "—",
                             "状态": "需前台电话补位"})

    n = len(rows)
    summary = [
        {"项": "评估日", "内容": payload.get("day", "")},
        {"项": "待执行预约", "内容": f"{n} 单"},
        {"项": "高风险单", "内容": f"{len(high)} 单（风险分 ≥ 12%）"},
        {"项": "历史基线", "内容": (f"门店爽约率 {payload.get('baseline_pct', 7.7)}%；"
                                f"未预付 12.3% / 有预付 3.1%；不指名 9.8% / 指名 5.2%")},
        {"项": "补位规则", "内容": ("一位爽约只通知一位候补，30 分钟未确认顺延下一位；"
                                f"发送时段 {payload['quiet_window'][1]}-{payload['quiet_window'][0]}，"
                                f"同一客户每日 ≤ {payload.get('daily_cap', 2)} 条")},
        {"项": "合规提示", "内容": "AI 生成内容；触达文案经老板确认后由前台发送，不使用诱导类话术"},
    ]
    at.ensure_outdir(outdir)
    xlsx = at.write_excel(
        os.path.join(outdir, "爽约风险清单.xlsx"),
        {"风险清单": rows, "补位排期": backfill or [{"触发时刻": "—", "风险单": "无高风险单",
                                              "候补客户": "—", "可到店": "—", "渠道": "—", "状态": "—"}],
         "汇总": summary},
        highlights={"风险清单": {"级别": "contains:高"}},
        widths={"风险清单": {"项目": 18, "建议动作": 30}},
    )
    png = at.bar_chart(
        os.path.join(outdir, "爽约风险.png"),
        [f"{r['客户']} {r['到店时间']}" for r in rows],
        [float(r["风险分"].rstrip("%")) for r in rows],
        title="各预约单爽约风险分（%，≥12% 高风险）", ylabel="风险分 %",
    )
    js = at.write_json({"risk_rows": rows, "backfill": backfill,
                        "summary": {r["项"]: r["内容"] for r in summary},
                        "generated_at": at.stamp(),
                        "note": "风险分由脚本按历史特征系数确定性计算；文案撰写与发送由模型+人工完成"},
                       os.path.join(outdir, "noshow_flow.json"))
    print(f"爽约评估完成 —— {n} 单中高风险 {len(high)} 单，补位排期 {len(backfill)} 条")
    for f in (xlsx, png, js):
        print(" 产物:", f)
    at.emit({"status": "ok", "files": [xlsx, png, js], "high": len(high)})


def main():
    ap = argparse.ArgumentParser(description="爽约预测与补位工作流 —— 特征打分 + 候补排期")
    ap.add_argument("--input", help="输入 JSON（bookings/waitlist/quiet_window）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    payload = DEMO if a.demo else (at.read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(payload, a.outdir)


if __name__ == "__main__":
    main()
