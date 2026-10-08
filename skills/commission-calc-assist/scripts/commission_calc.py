# -*- coding: utf-8 -*-
"""
提成计算辅助 —— 确定性核算脚本（四步算式全程可复算）。

职责边界（重要）：
  本脚本只做**确定性计算与产物生成**：项目提成计提、渠道扣点扣减、储值卡提成、
  合计与敏感性（费率 ±1 个百分点）测算、Excel/PNG 落盘。
  制度口径解释、与店长确认基数、进工资表的最终确认，由模型按 prompt.txt 完成。

计算规则（与 prompt.txt 一致）：
  R1 项目提成   项目收入 × 该品类计提比例（保留 2 位小数，逐项四舍五入后求和）
  R2 渠道扣点   命中渠道的项目收入 × 扣点比例，从提成总额中扣减
  R3 储值卡     卡面值 × 销售计提比例
  R4 敏感性     每类费率 ±1 个百分点对合计的影响额（元），逐项列出

用法：
  python commission_calc.py --input input.json --outdir out
  python commission_calc.py --demo --outdir out

产物：
  out/提成核算表.xlsx   测算明细 / 敏感性 / 汇总 三 sheet
  out/提成构成.png      各项目提成贡献柱状图
  out/commission.json   机器可读结果（供智能体/工作流读取）
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(SKILL_DIR))
sys.path.insert(0, os.path.join(REPO, "lib"))

try:
    import assettools as at
except ImportError:  # pragma: no cover
    print("[错误] 未找到 lib/assettools.py。请确认技能位于 <repo>/skills/<slug>/scripts/ 下。",
          file=sys.stderr)
    sys.exit(2)

at.need("pandas", "openpyxl", "matplotlib")

CATEGORY_CN = {"spa": "精油/香薰/全身 SPA", "massage": "推拿类", "care": "清洁护理类"}
CHANNEL_CN = {"meituan": "美团", "dianping": "大众点评", "miniapp": "微信小程序", "walkin": "门店散客"}


def demo_data():
    """轻语SPA滨江店 技师王丽 2026年9月核算（与 examples/input.json 同源的真实场景）。"""
    return {
        "store": "轻语SPA滨江店",
        "technician": "王丽",
        "month": "2026-09",
        "items": [
            {"name": "精油SPA 60分钟", "category": "spa", "unit_price": 298, "count": 12, "channel": "meituan"},
            {"name": "足部推拿45分钟", "category": "massage", "unit_price": 168, "count": 20, "channel": "walkin"},
            {"name": "小气泡深层清洁", "category": "care", "unit_price": 128, "count": 8, "channel": "miniapp"},
        ],
        "stored_card": {"count": 1, "face_value": 5000},
        "rules": {
            "category_rates": {"spa": 0.15, "massage": 0.12, "care": 0.10},
            "card_rate": 0.03,
            "channel_deduction": {"meituan": 0.05},
            "round": 2,
        },
    }


def calc(payload):
    """逐项计提 + 渠道扣点 + 储值卡 + 合计 + 敏感性。数值全部在此算出。"""
    rules = payload.get("rules") or {}
    cat_rates = rules.get("category_rates") or {}
    card_rate = float(rules.get("card_rate", 0.0))
    chan_ded = rules.get("channel_deduction") or {}
    nd = int(rules.get("round", 2))

    rows, total = [], 0.0
    for it in payload.get("items") or []:
        cat = it.get("category", "")
        rate = float(cat_rates.get(cat, 0.0))
        revenue = round(float(it.get("unit_price", 0)) * int(it.get("count", 0)), nd)
        commission = round(revenue * rate, nd)
        ded_rate = float(chan_ded.get(it.get("channel", ""), 0.0))
        deduction = round(revenue * ded_rate, nd)
        net = round(commission - deduction, nd)
        total = round(total + net, nd)
        rows.append({
            "项目": it.get("name", ""),
            "品类": CATEGORY_CN.get(cat, cat),
            "渠道": CHANNEL_CN.get(it.get("channel", ""), it.get("channel", "")),
            "单价(元)": float(it.get("unit_price", 0)),
            "次数": int(it.get("count", 0)),
            "项目收入(元)": revenue,
            "计提比例": f"{rate * 100:.0f}%",
            "项目提成(元)": commission,
            "渠道扣点(元)": -deduction if deduction else 0.0,
            "净提成(元)": net,
        })

    card = payload.get("stored_card") or {}
    card_commission = 0.0
    if card.get("face_value"):
        card_commission = round(float(card["face_value"]) * int(card.get("count", 1)) * card_rate, nd)
        total = round(total + card_commission, nd)
        rows.append({
            "项目": "储值卡销售", "品类": "储值卡", "渠道": "—",
            "单价(元)": float(card["face_value"]), "次数": int(card.get("count", 1)),
            "项目收入(元)": round(float(card["face_value"]) * int(card.get("count", 1)), nd),
            "计提比例": f"{card_rate * 100:.0f}%",
            "项目提成(元)": card_commission, "渠道扣点(元)": 0.0, "净提成(元)": card_commission,
        })

    # 敏感性：每类费率 ±1 个百分点对合计的影响
    sens = []
    for cat, rate in cat_rates.items():
        base = sum(r["项目收入(元)"] for r in rows if r["品类"] == CATEGORY_CN.get(cat, cat))
        impact = round(base * 0.01, nd)
        sens.append({"项目": f"{CATEGORY_CN.get(cat, cat)}计提比例 ±1 个百分点",
                     "影响(元)": f"{-impact if False else impact:+.2f} / {-impact:+.2f}"})
    for ch, rate in chan_ded.items():
        base = sum(r["项目收入(元)"] for r in rows if r["渠道"] == CHANNEL_CN.get(ch, ch))
        impact = round(base * 0.01, nd)
        sens.append({"项目": f"{CHANNEL_CN.get(ch, ch)}渠道扣点 ±1 个百分点",
                     "影响(元)": f"{-impact:+.2f} / {impact:+.2f}"})

    return {
        "store": payload.get("store", ""),
        "technician": payload.get("technician", ""),
        "month": payload.get("month", ""),
        "rows": rows,
        "sensitivity": sens,
        "total": total,
        "card_commission": card_commission,
    }


def write_outputs(res, outdir):
    at.ensure_outdir(outdir)
    files = []
    detail = res["rows"]
    summary = [
        {"项": "门店", "内容": res["store"]},
        {"项": "技师", "内容": res["technician"]},
        {"项": "核算月份", "内容": res["month"]},
        {"项": "计提项目数", "内容": str(len(detail))},
        {"项": "储值卡提成(元)", "内容": f"{res['card_commission']:.2f}"},
        {"项": "应发提成合计(元)", "内容": f"{res['total']:.2f}（测算草稿值，须财务复核）"},
        {"项": "合规提示", "内容": "AI 生成内容；进工资表前须经店长/财务人工复核"},
    ]
    xlsx = at.write_excel(
        os.path.join(outdir, "提成核算表.xlsx"),
        {"测算明细": detail, "敏感性": res["sensitivity"], "汇总": summary},
        widths={"测算明细": {"项目": 22, "品类": 18, "渠道": 12}},
    )
    files.append(xlsx)

    png = at.bar_chart(
        os.path.join(outdir, "提成构成.png"),
        [r["项目"] for r in detail],
        [r["净提成(元)"] for r in detail],
        title=f"{res['store']} {res['technician']} 提成构成（合计 {res['total']:.2f} 元）",
        ylabel="净提成（元）",
    )
    files.append(png)

    js = at.write_json({**res, "generated_at": at.stamp(),
                        "note": "数值均由本脚本计算并保留 2 位小数；制度口径解释由模型按 prompt.txt 完成"},
                       os.path.join(outdir, "commission.json"))
    files.append(js)
    return files


def main():
    ap = argparse.ArgumentParser(description="提成计算辅助（逐项计提 + 渠道扣点 + 敏感性）")
    ap.add_argument("--input", help="输入 JSON 路径")
    ap.add_argument("--outdir", default="out", help="输出目录")
    ap.add_argument("--demo", action="store_true", help="用内置演示数据运行")
    a = ap.parse_args()

    if a.demo:
        payload = demo_data()
    elif a.input:
        payload = at.read_json(a.input)
    else:
        ap.error("需提供 --input 或 --demo")

    if not payload.get("items"):
        print("[错误] items 为空：无计提明细不测算，请先补齐核销记录。", file=sys.stderr)
        sys.exit(3)

    res = calc(payload)
    files = write_outputs(res, a.outdir)
    print(f"核算完成 —— {res['store']} {res['technician']} {res['month']} "
          f"应发提成 {res['total']:.2f} 元（{len(res['rows'])} 个计提项）")
    for f in files:
        print(" 产物:", f, f"({os.path.getsize(f) / 1024:.1f} KB)")
    at.emit({"status": "ok", "files": files, "total": res["total"]})


if __name__ == "__main__":
    main()
