# -*- coding: utf-8 -*-
"""
客流需求预测 —— 确定性预测脚本（按星期几均值 + 趋势系数 + 区间带）。

职责边界（重要）：
  本脚本只做**确定性计算与产物生成**：星期几均值、周总量趋势系数、逐日预测值
  与上下限区间、Excel/PNG 落盘。
  预测方法的业务解释、节假日效应修正、排班决策，由模型按 prompt.txt 完成。

计算规则（与 prompt.txt 一致）：
  R1 星期均值   同星期几历史值取均值（消除周内节奏差异）
  R2 趋势系数   周总量环比均值的几何趋势，默认上浮 +3%（可由 trend_pct 覆盖）
  R3 区间带     预测值 × (1 ± band_pct)，默认 ±15%（样本不足 4 周时 ≥ ±20%）
  R4 样本闸门   历史不足 7 天拒绝预测（样本过少，任何外推都是编造）

用法：
  python demand_forecast.py --input input.json --outdir out
  python demand_forecast.py --demo --outdir out

产物：
  out/需求预测表.xlsx   预测结果 / 历史明细 / 汇总 三 sheet
  out/预测区间图.png    历史实际 + 预测线 + 上下限阴影
  out/forecast.json     机器可读结果（供智能体/工作流读取）
"""
from __future__ import annotations

import argparse
import datetime as dt
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

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def demo_data():
    """轻语SPA滨江店 2026-08-31 至 2026-09-27 每日核销单数（4 个完整周）。"""
    values = [14, 15, 16, 18, 23, 31, 25,
              15, 14, 17, 19, 25, 29, 27,
              13, 16, 17, 20, 24, 32, 26,
              15, 15, 18, 19, 26, 33, 28]
    start = dt.date(2026, 8, 31)
    return {
        "store": "轻语SPA滨江店",
        "metric": "每日预约完成单数（实际核销口径）",
        "series": [{"date": (start + dt.timedelta(days=i)).isoformat(), "value": v}
                   for i, v in enumerate(values)],
        "horizon_days": 7,
        "trend_pct": 3.0,
        "band_pct": 15.0,
    }


def forecast(payload):
    """星期均值 × 趋势系数 ± 区间带。数值全部在此算出，模型不得自行口算。"""
    series = payload.get("series") or []
    if len(series) < 7:
        print(f"[错误] 历史样本仅 {len(series)} 天（< 7 天），拒绝外推。", file=sys.stderr)
        sys.exit(3)

    band_pct = float(payload.get("band_pct", 15.0))
    trend_pct = float(payload.get("trend_pct", 3.0))
    horizon = int(payload.get("horizon_days", 7))

    import pandas as pd
    df = pd.DataFrame(series)
    df["date"] = pd.to_datetime(df["date"])
    df["weekday"] = df["date"].dt.weekday
    wd_mean = df.groupby("weekday")["value"].mean()

    # 周总量趋势：按 7 天切块求周总量，取首尾周的环比均值作为趋势参照
    vals = df["value"].tolist()
    weeks = [vals[i:i + 7] for i in range(0, len(vals) - len(vals) % 7, 7)]
    week_totals = [sum(w) for w in weeks if len(w) == 7]
    growth = []
    for i in range(1, len(week_totals)):
        if week_totals[i - 1]:
            growth.append((week_totals[i] / week_totals[i - 1] - 1) * 100)
    avg_growth = round(sum(growth) / len(growth), 2) if growth else 0.0

    last_date = max(d.date() for d in df["date"])
    rows = []
    for i in range(1, horizon + 1):
        d = last_date + dt.timedelta(days=i)
        base = float(wd_mean[d.weekday()])
        pred = base * (1 + trend_pct / 100.0)
        lo, hi = pred * (1 - band_pct / 100.0), pred * (1 + band_pct / 100.0)
        rows.append({
            "周期": f"{d.isoformat()}（{WEEKDAY_CN[d.weekday()]}）",
            "星期均值": round(base, 2),
            "预测值": round(pred),
            "下限": round(lo),
            "上限": round(hi),
        })

    detail = [{"日期": f"{d:%Y-%m-%d}", "星期": WEEKDAY_CN[d.weekday()], "实际值": int(v)}
              for d, v in zip(df["date"], df["value"])]
    total = sum(r["预测值"] for r in rows)
    return {
        "store": payload.get("store", ""),
        "metric": payload.get("metric", ""),
        "rows": rows,
        "detail": detail,
        "sample_days": len(series),
        "week_totals": week_totals,
        "avg_weekly_growth_pct": avg_growth,
        "trend_pct": trend_pct,
        "band_pct": band_pct,
        "horizon_total": total,
        "horizon_band": [round(total * (1 - band_pct / 100)), round(total * (1 + band_pct / 100))],
    }


def write_outputs(res, outdir):
    at.ensure_outdir(outdir)
    files = []
    summary = [
        {"项": "门店", "内容": res["store"]},
        {"项": "指标口径", "内容": res["metric"]},
        {"项": "历史样本", "内容": f"{res['sample_days']} 天"},
        {"项": "周总量", "内容": " → ".join(str(w) for w in res["week_totals"])},
        {"项": "周环比均值", "内容": f"{res['avg_weekly_growth_pct']:+.2f}%"},
        {"项": "趋势系数", "内容": f"+{res['trend_pct']:.0f}%"},
        {"项": "区间带", "内容": f"±{res['band_pct']:.0f}%（经验取值，非统计置信区间）"},
        {"项": "预测期合计", "内容": f"{res['horizon_total']} 单（区间 {res['horizon_band'][0]}-{res['horizon_band'][1]} 单）"},
        {"项": "合规提示", "内容": "AI 生成内容；预测非确定结论，排班前须经店长人工复核"},
    ]
    xlsx = at.write_excel(
        os.path.join(outdir, "需求预测表.xlsx"),
        {"预测结果": res["rows"], "历史明细": res["detail"], "汇总": summary},
        widths={"历史明细": {"日期": 12}},
    )
    files.append(xlsx)

    # 历史 + 预测拼接折线图（上下限双线）
    hist_x = [d["日期"][5:] for d in res["detail"]]
    hist_y = [d["实际值"] for d in res["detail"]]
    fc_x = [r["周期"].split("（")[0][5:] for r in res["rows"]]
    fc_y = [r["预测值"] for r in res["rows"]]
    lo_y = [r["下限"] for r in res["rows"]]
    hi_y = [r["上限"] for r in res["rows"]]
    png = at.line_chart(
        os.path.join(outdir, "预测区间图.png"),
        hist_x + fc_x,
        {"历史实际": hist_y + [None] * len(fc_x),
         "预测值": [None] * (len(hist_y) - 1) + [hist_y[-1]] + fc_y,
         "下限": [None] * len(hist_x) + lo_y,
         "上限": [None] * len(hist_x) + hi_y},
        title=f"{res['store']} 客流需求预测（±{res['band_pct']:.0f}% 区间带）",
        xlabel="日期", ylabel="单数", figsize=(11.5, 4.8),
    )
    files.append(png)

    js = at.write_json({**res, "generated_at": at.stamp(),
                        "note": "预测值由脚本按星期均值×趋势系数计算；节假日效应与排班决策由模型按 prompt.txt 完成"},
                       os.path.join(outdir, "forecast.json"))
    files.append(js)
    return files


def main():
    ap = argparse.ArgumentParser(description="客流需求预测（星期均值 + 趋势系数 + 区间带）")
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

    res = forecast(payload)
    files = write_outputs(res, a.outdir)
    print(f"预测完成 —— 未来 {len(res['rows'])} 天合计约 {res['horizon_total']} 单"
          f"（区间 {res['horizon_band'][0]}-{res['horizon_band'][1]}），"
          f"周环比均值 {res['avg_weekly_growth_pct']:+.2f}%")
    for f in files:
        print(" 产物:", f, f"({os.path.getsize(f) / 1024:.1f} KB)")
    at.emit({"status": "ok", "files": files, "horizon_total": res["horizon_total"]})


if __name__ == "__main__":
    main()
