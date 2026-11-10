# appointment-scheduler-zh 预约快照及候补状态台账

原文依据：提供历史爽约统计、当日待执行预约、候补名单与触达约束

# appointment-scheduler-zh 预约快照及候补状态台账

预约快照字段：store_id、snapshot_at/timezone、booking_id、source_channel、submitted_at、project_id、duration_minutes、named_staff_id、requested_start/window、prepayment_state、status、confirmed_at、cancelled_at。已有占用保留技师、房间、设备、开始结束和修订；订单取消须有实际状态，不能因为客户说取消就直接释放资源。

候补表另记waitlist_id、适用项目、可服务时间、轮转顺序、当前通知人、notification_operation_id、通知时刻、30分钟确认截止及回执。相同门店/订单/输入修订作为幂等键；一空位只给一位候补发出待确认邀请，超时先核原回执再顺延。排期建议不等于已占位或已通知。

自造示例：SIM门店订单B01，10:00起60分钟、未预付、状态requested；快照09:00，无真实联系方式。缺房型或技师技能时列unscheduled与所需字段，不能用原演示店配置填入。所有参数只在本门店当前快照内使用。

## 原资产定位

提供历史爽约统计、当日待执行预约、候补名单与触达约束

bangwozuo/appointment-scheduler-zh @ 4fd23ca30d0bfa86f06968f28c4bd04765a7dcf9 / workflows/no-show-predict-backfill-flow/SKILL.md

本资源区分源方法、当前产品规则、待填客户配置和待核外部条件；仿真不得当客户实绩。
