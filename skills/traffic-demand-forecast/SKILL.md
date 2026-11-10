---
name: traffic-demand-forecast
description: 帮我做排班调度师的客流预测能力，调用准确已安装员工，保留原事项、真实文件及人工核对。
---

# 客流预测

读取同包 references/connection.example.json 和 scripts/product.py。先contract核对准确安装，fields.operation固定为forecast，data采用当前服务对应examples的结构；原条件、知识和完整来源属性见definition.json。输入契约见native-schema.json；prompt.txt保留原方法/流程全文，不能把旧占位说明当作当前运行证据。

submit仅提交一次并保留原业务编号；丢响应先lookup同一事项，不换号或自动重试。review保存修订意见、确认或退回准确当前稿，并回读原生终态。缺门店配置、价目、人员设备快照或回执时返回缺项，不编造。当前为明确合成资料接收，未向客户发送、写真实预约、发工资或证明经营收益。

