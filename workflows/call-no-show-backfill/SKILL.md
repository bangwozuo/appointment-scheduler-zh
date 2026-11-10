---
name: call-no-show-backfill
description: 帮我做排班调度师的爽约风险与候补能力，调用准确已安装员工，保留原事项、真实文件及人工核对。
---

# 爽约风险与候补

读取同包 references/connection.example.json 和 scripts/product.py。先contract核对准确安装，fields.operation固定为no_show，data采用当前服务对应examples的结构；原条件、知识和完整来源属性见definition.json。输入契约见native-schema.json；prompt.txt保留原方法/流程全文，不能把旧占位说明当作当前运行证据。

submit仅提交一次并保留原业务编号；丢响应先lookup同一事项，不换号或自动重试。review保存修订意见、确认或退回准确当前稿，并回读原生终态。缺门店配置、价目、人员设备快照或回执时返回缺项，不编造。当前为明确合成资料接收，未向客户发送、写真实预约、发工资或证明经营收益。

data含risk、rotation及expected_ledger_revision；原候补台账由服务端读取，不能用客户端列表覆盖。sent/confirmed/declined必须有明确回执事实；草稿确认不等于发送，30分钟从实际发送回执时点计算。
本Skill调用现有原生SOP；宿主不另建工作流、团队、检查点、日程或Agent循环。
