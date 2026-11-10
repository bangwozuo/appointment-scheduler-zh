"""A bounded waitlist transition, driven only by explicit current receipts.

The native workflow/Store holds state; this function has no timer, connector,
execution loop or authority to label a draft as a message actually sent.
"""
from datetime import datetime,timedelta
from copy import deepcopy


def aware(value):
    result=datetime.fromisoformat(value)
    if result.utcoffset() is None:raise ValueError('Explicit business timezone required')
    return result


def rotate(data):
    required=['store_id','slot','candidates','ledger','now','window','daily_cap','contact_history']
    missing=[key for key in required if key not in data]
    if missing:return {'status':'needs_input','missing_fields':missing}
    now=aware(data['now']);slot=data['slot'];start=aware(slot['start']);end=aware(slot['end'])
    if not data['store_id'] or not slot['id'] or start>=end or now.utcoffset()!=start.utcoffset():
        raise ValueError('Invalid store, slot or timezone')
    cap=data['daily_cap']
    if type(cap)is not int or not 1<=cap<=2:raise ValueError('Source daily cap is at most two')
    opening,closing=[datetime.strptime(x,'%H:%M').time()for x in data['window']]
    if not datetime.strptime('09:00','%H:%M').time()<=opening<closing<=datetime.strptime('21:30','%H:%M').time():
        raise ValueError('Outside the frozen contact window')
    ledger=deepcopy(data['ledger'])
    if any(row['store_id']!=data['store_id']or row['slot_id']!=slot['id']for row in ledger):
        raise ValueError('Waitlist ledger belongs to a different store or slot')
    ids=[row['customer_id']for row in data['candidates']]
    if len(ids)!=len(set(ids)):raise ValueError('Duplicate candidate')
    active=[row for row in ledger if row['state']in {'draft','sent','confirmed'}]
    if len(active)>1:raise ValueError('One slot cannot have two active candidates')
    result={'status':'draft','ledger':ledger,'proposal':None,'actually_sent':False,'external_booking_created':False}
    if active:
        row=active[0]
        if row['state']=='confirmed':return {**result,'decision':'slot_filled'}
        if row['state']=='draft':return {**result,'decision':'await_owner_and_front_desk','proposal':row}
        sent=aware(row['sent_at'])
        if sent>now:raise ValueError('Future sending receipt')
        if now<sent+timedelta(minutes=30):
            return {**result,'decision':'await_customer','deadline':(sent+timedelta(minutes=30)).isoformat()}
        # Timeout is anchored to an explicit actual-send receipt, never draft time.
        row['state']='expired';row['expired_at']=now.isoformat()
    if now>=start:return {**result,'decision':'slot_closed'}
    if not opening<=now.time().replace(tzinfo=None)<=closing:
        return {**result,'decision':'quiet_window'}
    attempted={row['customer_id']for row in ledger}
    ordered=sorted(enumerate(data['candidates']),key=lambda pair:(pair[1]['rotation_order'],pair[0]))
    for _,candidate in ordered:
        customer=candidate['customer_id']
        if customer in attempted or slot['service']not in candidate['services']:continue
        if aware(candidate['available_from'])>start or aware(candidate['available_to'])<end:continue
        history=[row for row in data['contact_history']if row['store_id']==data['store_id']and row['customer_id']==customer
                 and aware(row['at']).astimezone(now.tzinfo).date()==now.date()and row['state']in {'reserved','approved','sent'}]
        if len({row['id']for row in history})>=cap:continue
        row={'store_id':data['store_id'],'slot_id':slot['id'],'customer_id':customer,
             'state':'draft','proposed_at':now.isoformat(),'approval':'pending_owner',
             'requires_front_desk_send_receipt':True,'internal_risk_label_exposed':False}
        ledger.append(row)
        return {**result,'decision':'one_candidate_proposed','proposal':row}
    return {**result,'decision':'no_eligible_candidate'}


def receipt(ledger,customer_id,event,at):
    """Apply an explicitly supplied receipt. Caller owns authorization and CAS."""
    rows=deepcopy(ledger);active=[r for r in rows if r['customer_id']==customer_id and r['state']in {'draft','sent'}]
    if len(active)!=1:raise ValueError('No unique current candidate')
    row=active[0];aware(at)
    if event=='sent':
        if row['state']!='draft':raise ValueError('Sending already recorded')
        row.update(state='sent',sent_at=at)
    elif event in {'confirmed','declined'}:
        if row['state']!='sent'or aware(at)<aware(row['sent_at']):raise ValueError('Customer reply needs the original prior send receipt')
        row.update(state=event,response_at=at)
    else:raise ValueError('Unknown receipt event')
    return rows
