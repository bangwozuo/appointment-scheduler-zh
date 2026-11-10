"""Full source notification timing; drafts only, no sending or scheduler."""
from datetime import datetime,timedelta
from collections import Counter
from server.appointment_rules import minute,required


def notification(data):
    missing=required(data,['items','history','window','daily_cap','store_address','now'])
    if missing:return missing
    now=datetime.fromisoformat(data['now'])
    if now.utcoffset()is None:raise ValueError('Explicit business timezone required')
    opening,closing=map(minute,data['window']);cap=data['daily_cap']
    if not 540<=opening<closing<=1290 or type(cap)is not int or not 1<=cap<=2:raise ValueError('Invalid allowed window or frequency cap')
    before_window=data.get('previous_day_window',['15:00','16:00'])
    before_open,before_close=map(minute,before_window)
    if not opening<=before_open<=before_close<=closing:raise ValueError('Day-before window outside allowed hours')
    history=data['history'];counts=Counter();previous=set()
    for row in history:
        at=datetime.fromisoformat(row['at'])
        if at.utcoffset()!=now.utcoffset():raise ValueError('History timezone differs')
        # Includes pending approved drafts/reservations, not only sent events.
        # The caller supplies the same persistent ledger across all flows.
        if row.get('state','sent')in {'sent','reserved','approved'}:counts[(row['customer_id'],at.date().isoformat())]+=1
        previous.add(row['id'])
    drafts,conflicts,unscheduled=[],[],[];seen=set()
    for item in data['items']:
        if item['id']in seen:raise ValueError('Duplicate notification id')
        seen.add(item['id'])
        if item['id']in previous:
            unscheduled.append({'id':item['id'],'reason':'already_recorded; no replay'});continue
        appointment=datetime.fromisoformat(item['appointment_at'])
        if appointment.utcoffset()!=now.utcoffset():raise ValueError('Appointment timezone differs')
        offset=item['before_minutes']
        if offset not in {30,120,1440}:raise ValueError('Unsupported original trigger')
        if offset==1440:
            previous_day=(appointment-timedelta(days=1)).replace(hour=before_open//60,minute=before_open%60,second=0,microsecond=0)
            deadline=previous_day.replace(hour=before_close//60,minute=before_close%60)
            proposed=max(previous_day,now)
            if proposed>deadline:
                unscheduled.append({'id':item['id'],'reason':'previous_day_window_missed','window':[previous_day.isoformat(),deadline.isoformat()]});continue
            timing='previous_day_configured_window'
        else:
            proposed=max(appointment-timedelta(minutes=offset),now);timing='before_appointment_minutes'
        local=proposed.hour*60+proposed.minute
        if local<opening:proposed=proposed.replace(hour=opening//60,minute=opening%60,second=0,microsecond=0)
        elif local>closing:proposed=(proposed+timedelta(days=1)).replace(hour=opening//60,minute=opening%60,second=0,microsecond=0)
        if proposed>=appointment:
            unscheduled.append({'id':item['id'],'reason':'quiet_window_or_late_input_exceeds_appointment'});continue
        channel='wechat'if item['wechat_followed']is True and item['wechat_eligibility_verified']is True else'sms_unverified_draft'
        customer=item['customer_id'];day=proposed.date().isoformat()
        same=next((x for x in drafts if x['customer_id']==customer and x['at'][:10]==day and x['channel']==channel),None)
        if not same and counts[(customer,day)]>=cap:
            unscheduled.append({'id':item['id'],'reason':'daily_frequency_cap'});continue
        text=f"{item['service']}；预约{appointment.isoformat()}；技师{item['technician']}；{data['store_address']}；改期/取消：{item['change_route']}"
        if item['no_show_count']>=2:text+='；请回复1确认/回复2改期'
        if same:
            # Sending later than the earliest combined trigger would silently
            # miss a stricter reminder. Preserve the earliest allowed time.
            same['at']=min(same['at'],proposed.isoformat());same['source_ids'].append(item['id']);same['messages'].append(text)
            conflicts.append({'id':item['id'],'resolution':'merged_with_current_customer_day_draft'})
        else:
            drafts.append({'customer_id':customer,'source_ids':[item['id']],'at':proposed.isoformat(),'channel':channel,
                'messages':[text],'timing_rule':timing,'approval':'pending_owner',
                'send_executor':'front_desk_after_owner_confirmation','actually_sent':False});counts[(customer,day)]+=1
    return {'status':'draft','schedule':drafts,'conflicts':conflicts,'unscheduled':unscheduled,'actually_sent':False,
            'AI_generated':True,'marketing_copy_generated':False,'previous_day_window':before_window,
            'assumptions':[]if'previous_day_window'in data else['source default day-before15:00–16:00; explicit current configuration may narrow it'],
            'history_scope':'caller-supplied persistent cross-flow ledger; absent connector does not prove external history complete'}
