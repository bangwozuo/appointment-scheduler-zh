"""Executable, source-derived scheduling and notification draft rules.

No connector, timer, external write, model loop or host authority is introduced.
Inputs must contain the current resource/configuration facts explicitly.
"""
from datetime import datetime, timedelta, time
from collections import Counter


def required(data, names):
    missing = [key for key in names if key not in data]
    return {'status': 'needs_input', 'missing_fields': missing} if missing else None


def minute(value):
    if not isinstance(value, str):
        raise ValueError('Time must use HH:MM')
    parsed = datetime.strptime(value, '%H:%M')
    return parsed.hour * 60 + parsed.minute


def hm(value):
    return f'{value // 60:02}:{value % 60:02}'


def overlap(a, b, c, d):
    return a < d and c < b


def schedule(data):
    missing = required(data, ['items', 'technicians', 'rooms', 'equipment', 'existing',
                              'open', 'close', 'last_start', 'buffer_min', 'step_min'])
    if missing:
        return missing
    opening, closing, last = map(minute, [data['open'], data['close'], data['last_start']])
    buffer, step = data['buffer_min'], data['step_min']
    if type(buffer) is not int or buffer < 15 or type(step) is not int or not 1 <= step <= 60:
        raise ValueError('Explicit buffer >= 15 minutes and bounded slot step required')
    if not opening < closing or not opening <= last <= closing - 60:
        raise ValueError('Last start must respect closing minus 60 minutes')
    techs, rooms, equipment = data['technicians'], data['rooms'], data['equipment']
    if len({x['id'] for x in techs}) != len(techs) or len({x['id'] for x in rooms}) != len(rooms):
        raise ValueError('Duplicate resource identity')
    if any(type(v) is not int or v < 1 for v in equipment.values()):
        raise ValueError('Equipment capacity must be a positive integer')
    items = data['items']
    if len({x['id'] for x in items}) != len(items):
        raise ValueError('Duplicate request identity')
    occupied = []
    for x in data['existing']:
        a, b = minute(x['start']), minute(x['end'])
        if a >= b or x['technician'] not in {t['id'] for t in techs} or x['room'] not in {r['id'] for r in rooms}:
            raise ValueError('Invalid existing reservation')
        occupied.append({**x, 'a': a, 'b': b, 'equipment': list(x['equipment'])})
    assigned, conflicts, unscheduled = [], [], []
    # Named requests first, then requested time and stable submission order.
    ordered = sorted(enumerate(items), key=lambda pair: (
        not bool(pair[1].get('named_technician')), minute(pair[1]['preferred']), pair[0]))
    for _, item in ordered:
        duration = item['duration_min']
        if type(duration) is not int or duration <= 0 or duration > closing - opening:
            raise ValueError('Invalid service duration')
        wanted = minute(item['preferred'])
        earliest, latest = minute(item['earliest']), min(minute(item['latest']), last)
        needs = list(item['equipment'])
        if len(needs) != len(set(needs)):
            raise ValueError('Equipment requirements must be unique; quantities use resource capacity')
        if set(needs) - set(equipment):
            unscheduled.append({'id': item['id'], 'reason': 'equipment_not_configured',
                                'missing': sorted(set(needs) - set(equipment))})
            continue
        candidates, reasons = [], set()
        for t in techs:
            if item.get('named_technician') and t['id'] != item['named_technician']:
                continue
            if item['service'] not in t['services']:
                reasons.add('technician_service_mismatch')
                continue
            for room in rooms:
                if room['type'] != item['room_type']:
                    reasons.add('room_type_mismatch')
                    continue
                for start in range(max(earliest, opening), latest + 1, step):
                    end = start + duration
                    if end > closing or start < minute(t['shift'][0]) or end + buffer > minute(t['shift'][1]):
                        reasons.add('shift_or_close')
                        continue
                    if any(overlap(start, end + buffer, minute(a), minute(b)) for a, b in t['breaks']):
                        reasons.add('break_or_training')
                        continue
                    sharing = [x for x in occupied if overlap(start, end + buffer, x['a'], x['b'] + buffer)]
                    if any(x['technician'] == t['id'] or x['room'] == room['id'] for x in sharing):
                        reasons.add('person_or_room_conflict')
                        continue
                    # Capacity is checked at each interval boundary, not by
                    # summing non-overlapping bookings across the whole slot.
                    points = {start, *[max(start, x['a']) for x in sharing]}
                    if any(sum(x['a'] <= point < x['b'] + buffer and e in x['equipment']
                               for x in occupied) >= equipment[e]
                           for e in needs for point in points):
                        reasons.add('equipment_conflict')
                        continue
                    load = sum(x['b'] - x['a'] for x in occupied if x['technician'] == t['id'])
                    room_load = sum(x['b'] - x['a'] for x in occupied if x['room'] == room['id'])
                    candidates.append((abs(start - wanted), load, room_load, start, t['id'], room['id']))
        if not candidates:
            unscheduled.append({'id': item['id'], 'reason': sorted(reasons) or ['named_technician_unavailable']})
            continue
        _, _, _, start, technician, room = min(candidates)
        row = {'id': item['id'], 'technician': technician, 'room': room,
               'start': hm(start), 'end': hm(start + duration), 'equipment': needs,
               'changed_time': start != wanted, 'requires_customer_confirmation': start != wanted,
               'approval': 'pending_manager', 'external_booking_created': False}
        assigned.append(row)
        occupied.append({**row, 'a': start, 'b': start + duration})
        if start != wanted:
            conflicts.append({'id': item['id'], 'requested': hm(wanted), 'proposed': hm(start),
                              'resolution': 'rechecked_alternative_draft', 'constraints_rechecked': True})
    utilization = []
    for t in techs:
        available = minute(t['shift'][1]) - minute(t['shift'][0]) - sum(minute(b) - minute(a) for a, b in t['breaks'])
        if available <= 0:
            raise ValueError('No positive technician working capacity')
        used = sum(x['b'] - x['a'] for x in occupied if x['technician'] == t['id'])
        ratio = round(used / available * 100, 2)
        utilization.append({'technician': t['id'], 'percent': ratio,
                            'flag': 'overload' if ratio > 85 else 'backfill_opportunity' if ratio < 30 else 'normal'})
    assert len(assigned) + len(unscheduled) == len(items)
    return {'status': 'draft', 'schedule': assigned, 'conflicts': conflicts, 'unscheduled': unscheduled,
            'utilization': utilization, 'algorithm': 'source-priority deterministic greedy; no global optimum claim',
            'actually_published': False, 'AI_generated': True}


def notification(data):
    missing = required(data, ['items', 'history', 'window', 'daily_cap', 'store_address', 'now'])
    if missing:
        return missing
    now = datetime.fromisoformat(data['now'])
    if now.utcoffset() is None:
        raise ValueError('Explicit business timezone required')
    opening, closing = map(minute, data['window'])
    cap = data['daily_cap']
    if not 9 * 60 <= opening < closing <= 21 * 60 + 30 or type(cap) is not int or not 1 <= cap <= 2:
        raise ValueError('Window or per-customer daily cap exceeds frozen source rules')
    history = data['history']
    if any(datetime.fromisoformat(x['at']).utcoffset() != now.utcoffset() for x in history):
        raise ValueError('History timezone must match current configuration')
    counts = Counter((x['customer_id'], datetime.fromisoformat(x['at']).date().isoformat()) for x in history)
    previous = {x['id'] for x in history}
    drafts, unscheduled, conflicts = [], [], []
    seen = set()
    for item in data['items']:
        if item['id'] in seen:
            raise ValueError('Duplicate notification identity')
        seen.add(item['id'])
        if item['id'] in previous:
            unscheduled.append({'id': item['id'], 'reason': 'already_recorded; no replay'})
            continue
        at = datetime.fromisoformat(item['appointment_at'])
        if at.utcoffset() != now.utcoffset():
            raise ValueError('Appointment timezone must match configuration')
        offset = item['before_minutes']
        if offset not in {30, 120, 1440}:
            raise ValueError('Unsupported trigger; use original day-before/30min/2h rules')
        proposed = max(at - timedelta(minutes=offset), now)
        local_min = proposed.hour * 60 + proposed.minute
        if local_min < opening:
            proposed = proposed.replace(hour=opening // 60, minute=opening % 60, second=0, microsecond=0)
        elif local_min > closing:
            proposed = (proposed + timedelta(days=1)).replace(hour=opening // 60, minute=opening % 60, second=0, microsecond=0)
        if proposed >= at:
            unscheduled.append({'id': item['id'], 'reason': 'quiet_window_or_late_input_exceeds_appointment'})
            continue
        channel = 'wechat' if item['wechat_followed'] is True and item['wechat_eligibility_verified'] is True else 'sms_unverified_draft'
        customer, day = item['customer_id'], proposed.date().isoformat()
        same = next((x for x in drafts if x['customer_id'] == customer and x['at'][:10] == day and x['channel'] == channel), None)
        if not same and counts[(customer, day)] >= cap:
            unscheduled.append({'id': item['id'], 'reason': 'daily_frequency_cap'})
            continue
        text = f"{item['service']}；预约{at.isoformat()}；技师{item['technician']}；{data['store_address']}；改期/取消：{item['change_route']}"
        if item['no_show_count'] >= 2:
            text += '；请回复1确认/回复2改期'
        if same:
            same['source_ids'].append(item['id']);same['messages'].append(text)
            conflicts.append({'id': item['id'], 'resolution': 'merged_with_current_customer_day_draft'})
        else:
            drafts.append({'customer_id': customer, 'source_ids': [item['id']], 'at': proposed.isoformat(),
                           'channel': channel, 'messages': [text], 'approval': 'pending_owner',
                           'send_executor': 'front_desk_after_owner_confirmation', 'actually_sent': False})
            counts[(customer, day)] += 1
        if proposed != at - timedelta(minutes=offset):
            conflicts.append({'id': item['id'], 'resolution': 'moved_into_allowed_window', 'at': proposed.isoformat()})
    return {'status': 'draft', 'schedule': drafts, 'conflicts': conflicts, 'unscheduled': unscheduled,
            'actually_sent': False, 'AI_generated': True, 'marketing_copy_generated': False}
