# appointment-scheduler-zh 客户授权触达与执行配置

原文依据：经营数据含门店商业信息，不对外披露；客户名单按授权范围使用

# appointment-scheduler-zh 客户授权触达与执行配置

门店触达配置表：store_id、customer_pseudonym、consent_scope/source/valid_until、channel_eligibility、official_account_followed、masked_contact、group、service_or_marketing、daily_contact_count、timezone、change_url、cancel_url、owner_review_revision、execution_owner及actual_delivery_receipt。联系方式、老客与次卡分群不跨门店复用；已有公众号关注也不自动代表营销许可。

排期读取同一客户当天全部相关流程已触达数，剩余额度不足则列不排入原因；同一通知的刷新不增加次数，未知发送先按原operation_id查证。改期取消入口须可用且属当前门店，缺入口不能编网址。老板确认绑定实际文案/客户范围/排期修订，前台执行后登记真实回执。

仿真仅使用SIM-C01，无真实电话，followed=true、当天已触达2条，则该日新增提醒不得排为可发送。用户拒绝触达或许可到期停止新排期。没有实际连接器时delivery_state保持not_sent，导出名单不等于已触达。

## 原资产定位

经营数据含门店商业信息，不对外披露；客户名单按授权范围使用

bangwozuo/appointment-scheduler-zh @ 4fd23ca30d0bfa86f06968f28c4bd04765a7dcf9 / workflows/scheduling-efficiency-weekly-flow/SKILL.md

本资源区分源方法、当前产品规则、待填客户配置和待核外部条件；仿真不得当客户实绩。
