# appointment-scheduler-zh 门店需求爽约与容量历史

原文依据：提供上周分时段到店人次、时段容量、周趋势与触达约束

# appointment-scheduler-zh 门店需求爽约与容量历史

历史数据表按store_id、date/timezone、slot_id、metric、value、unit、source_position、snapshot_revision登记。实际核销、取消、爽约与预约提交是不同指标，必须记录统计分母、状态截止和去重方法；不把未到时间的预约算爽约，也不从缺失行推0。预付、定金、全款与指名信息保留原状态及有效时点。

容量资料按时段、工作日/周末、可用技师、房间、设备和不可用时间登记；上周实际与周趋势记录原件及是否含节假日、停业、营销。先核日期连续性、重复核销、异常负数、单位和口径一致，再给计算工具。取消状态待确认或历史不足时返回缺项，避免模型补齐序列。

仿真可以提供一周7条每天10人次，并清楚写data_origin=SYNTHETIC；它只用于预测计算和拒绝缺失用例，不证明真实准确率、爽约改善或营收。客户资料未配置时保留模板，不要求用户本轮提供，不把原演示数据冒作实际门店导出。

## 原资产定位

提供上周分时段到店人次、时段容量、周趋势与触达约束

bangwozuo/appointment-scheduler-zh @ 4fd23ca30d0bfa86f06968f28c4bd04765a7dcf9 / workflows/scheduling-efficiency-weekly-flow/SKILL.md

本资源区分源方法、当前产品规则、待填客户配置和待核外部条件；仿真不得当客户实绩。
