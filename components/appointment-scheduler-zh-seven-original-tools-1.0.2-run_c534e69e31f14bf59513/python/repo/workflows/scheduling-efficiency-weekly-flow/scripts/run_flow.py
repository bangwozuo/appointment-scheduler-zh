# -*- coding: utf-8 -*-
"""
排班效率周报工作流 —— 端到端编排脚本。

流程（与 SKILL.md 的 DAG 一致）：
  S1 上座率热力图    本步（脚本承担）确定性计算：分时段到店人次 ÷ 时段容量
                     = 上座率，按 <60% 低谷 / 60%-85% 正常 / >85% 满负荷分级
  S2 客流需求预测    本步（脚本承担）下周分时段预测 = 上周实际 × 周环比系数
  S3 低谷促销建议    本步（脚本承担）为低谷时段生成触达排期骨架（触发时刻/渠道/频控）
  S4 人工确认        老板确认促销方案与文案后发布

确定性规则（与 prompt.txt 一致）：
  R1 容量口径   每时段周容量 = 工作日容量 × 5 + 周末容量 × 2（本 demo 为 50 人次/时段）
  R2 分级阈值   上座率 <60% 低谷（促销候选）/ 60%-85% 正常 / >85% 满负荷（限流或加人）
  R3 预测口径   下周预测 = 上周实际 × (1 + 周环比系数)；周环比连续 2 周为负时降档系数
  R4 触达频控   发送时段 09:00-21:30；同一客户每日 ≤ 2 条；促销文案须老板确认后发布

用法：
  python run_flow.py --input input.json --outdir out
  python run_flow.py --demo

产物：
  out/排班效率周报.xlsx   上座率 / 下周预测 / 触达排期 / 汇总 四 sheet
  out/上座率热力.png      各时段上座率柱状图（阈值线 60%/85%）
  out/weekly_flow.json    机器可读结果
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

LOW, HIGH = 60.0, 85.0   # 上座率分级阈值（%）
WEEKDAY_CAP, WEEKEND_CAP = 6, 10   # 每时段容量（人次）：工作日 6 / 周末 10


def demo_data():
    buckets = ["10:00-12:00", "12:00-14:00", "14:00-16:00", "16:00-18:00", "18:00-20:00"]
    actual = [18, 21, 35, 38, 44]
    return {
        "week": "2026-09-21 至 2026-09-27",
        "next_week": "2026-09-28 至 2026-10-04",
        "week_total_trend_pct": 3.0,      # 周环比 +3%
        "weekday_cap": WEEKDAY_CAP,
        "weekend_cap": WEEKEND_CAP,
        "buckets": [{"bucket": b, "actual": a} for b, a in zip(buckets, actual)],
        "quiet_window": ("21:30", "09:00"),
        "daily_cap": 2,
    }


def grade(u):
    return "低谷（促销候选）" if u < LOW else ("正常" if u <= HIGH else "满负荷（限流或加人）")


def build(payload, outdir):
    if not payload.get("buckets"):
        raise SystemExit("[错误] buckets 为空：无分时段数据不出周报，请先导出上周到店人次")
    wd_cap = int(payload.get("weekday_cap", WEEKDAY_CAP))
    we_cap = int(payload.get("weekend_cap", WEEKEND_CAP))
    weekly_cap = wd_cap * 5 + we_cap * 2
    trend = float(payload.get("week_total_trend_pct", 3.0))

    rows = []
    for b in payload["buckets"]:
        actual = int(b["actual"])
        util = actual / weekly_cap * 100
        fc = round(actual * (1 + trend / 100.0))
        rows.append({
            "时段": b["bucket"], "上周到店(人次)": actual,
            "时段周容量(人次)": weekly_cap,
            "上座率": f"{util:.1f}%",
            "评级": grade(util),
            "下周预测(人次)": fc,
            "预测上座率": f"{fc / weekly_cap * 100:.1f}%",
            "建议": ("低谷：错峰券/老客专属时段定向触达" if util < LOW else
                    "维持当前排班" if util <= HIGH else "满负荷：加技师或引导错峰"),
        })
    troughs = [r for r in rows if r["上座率"] and float(r["上座率"].rstrip("%")) < LOW]
    avg_util = sum(float(r["上座率"].rstrip("%")) for r in rows) / len(rows)

    # 低谷触达排期骨架：提前 1 天于 10:00 触达（避开 21:30 后夜间静默）
    outreach = []
    for t in troughs:
        outreach.append({
            "触达日": "低谷日前一天 10:00", "目标时段": t["时段"],
            "目标人群": "历史该时段到店老客 + 次卡临期客户",
            "渠道": "微信服务通知（关注公众号客户）/ 短信（未关注客户）",
            "频控": (f"同一客户每日 ≤ {payload.get('daily_cap', 2)} 条；"
                    f"发送时段 {payload['quiet_window'][1]}-{payload['quiet_window'][0]}"),
            "状态": "文案与价格须经老板确认后发布",
        })

    summary = [
        {"项": "统计周", "内容": payload.get("week", "")},
        {"项": "平均上座率", "内容": f"{avg_util:.1f}%"},
        {"项": "低谷时段", "内容": "、".join(t["时段"] for t in troughs) if troughs else "无（全部 ≥60%）"},
        {"项": "容量口径", "内容": (f"工作日 {wd_cap} 人次/时段 × 5 + 周末 {we_cap} 人次/时段 × 2 "
                                f"= {weekly_cap} 人次/时段·周")},
        {"项": "下周趋势", "内容": f"按周环比 +{trend:.0f}% 外推（连续 2 周为负时降档）"},
        {"项": "触达约束", "内容": ("发送时段 09:00-21:30；同一客户每日 ≤ 2 条；"
                                "不使用诱导类话术；促销文案经老板确认后发布")},
        {"项": "合规提示", "内容": "AI 生成内容；促销方案与排班调整须经人工确认"},
    ]
    at.ensure_outdir(outdir)
    xlsx = at.write_excel(
        os.path.join(outdir, "排班效率周报.xlsx"),
        {"上座率": rows, "触达排期": outreach or [{"触达日": "—", "目标时段": "无低谷时段"}],
         "汇总": summary},
        highlights={"上座率": {"评级": "contains:低谷"}},
        widths={"上座率": {"建议": 30}, "触达排期": {"目标人群": 28, "频控": 34}},
    )
    png = at.bar_chart(
        os.path.join(outdir, "上座率热力.png"),
        [r["时段"] for r in rows],
        [float(r["上座率"].rstrip("%")) for r in rows],
        title=f"分时段上座率（{payload.get('week', '')}，阈值线 60%/85%）",
        ylabel="上座率 %",
    )
    js = at.write_json({"rows": rows, "outreach": outreach,
                        "summary": {r["项"]: r["内容"] for r in summary},
                        "generated_at": at.stamp(),
                        "note": "上座率/预测/排期骨架由脚本确定性计算；促销文案与发布由模型+人工完成"},
                       os.path.join(outdir, "weekly_flow.json"))
    print(f"周报完成 —— 平均上座率 {avg_util:.1f}%，低谷 {len(troughs)} 个时段，"
          f"触达排期 {len(outreach)} 条")
    for f in (xlsx, png, js):
        print(" 产物:", f)
    at.emit({"status": "ok", "files": [xlsx, png, js], "troughs": len(troughs)})


def main():
    ap = argparse.ArgumentParser(description="排班效率周报工作流 —— 上座率/预测/低谷触达")
    ap.add_argument("--input", help="输入 JSON（buckets/weekday_cap/weekend_cap/trend）")
    ap.add_argument("--outdir", default="out")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    payload = demo_data() if a.demo else (at.read_json(a.input) if a.input else ap.error("需要 --input 或 --demo"))
    build(payload, a.outdir)


if __name__ == "__main__":
    main()
