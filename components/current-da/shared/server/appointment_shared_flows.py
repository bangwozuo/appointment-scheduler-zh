"""Three original workflows consume the same versioned resource algorithm.

The caller supplies an authorized versioned resource snapshot. Results remain
proposals; neither customer confirmation nor an external reservation is forged.
"""
from copy import deepcopy
from .appointment_rules import schedule, minute


def shared_flow(operation, data):
    if operation not in {'booking', 'scheduling', 'conflict'}:
        raise ValueError('Unknown shared-resource consumer')
    missing = [k for k in ('store_id', 'snapshot_id', 'resource_snapshot') if k not in data]
    if operation == 'booking':
        missing += [k for k in ('pricebook',) if k not in data]
    if missing:
        return {'status': 'needs_input', 'missing_fields': missing}
    if not data['store_id'] or not data['snapshot_id']:
        raise ValueError('Exact store and snapshot identity required')
    payload = deepcopy(data['resource_snapshot'])
    if operation == 'booking':
        if len(payload.get('items', [])) != 1:
            raise ValueError('Intake requires exactly one original request')
        item = payload['items'][0]
        price = data['pricebook'].get(item.get('service'))
        if not isinstance(price, dict) or not {'duration_min', 'price'} <= set(price):
            return {'status': 'needs_input', 'missing_fields': ['pricebook.' + str(item.get('service'))]}
        if type(price['price']) not in {int, float} or price['price'] < 0 or price['duration_min'] != item.get('duration_min'):
            raise ValueError('Price/duration must match the selected service')
        # Original intake promises the earliest eligible five-minute slot.
        # Keep the requested window, named technician and every hard constraint.
        payload['step_min'] = 5
        item['preferred'] = item['earliest']
    result = schedule(payload)
    if result['status'] == 'needs_input':
        return result
    result.update(operation=operation, store_id=data['store_id'], snapshot_id=data['snapshot_id'],
                  shared_capability='smart-scheduling-engine', external_booking_created=False,
                  customers_confirmed=False, manager_confirmed=False)
    if operation == 'booking':
        result['accepted'] = bool(result['schedule'])
        result['slot'] = result['schedule'][0] if result['schedule'] else None
        result['price'] = price['price']
        result['steps'] = [
            {'check': 'exact_service_price_and_duration', 'status': 'passed'},
            {'check': 'person_room_equipment_shift_buffer', 'status': 'passed' if result['accepted'] else 'blocked'},
            {'check': 'customer_confirmation', 'status': 'pending'},
            {'check': 'front_desk_review_before_write', 'status': 'pending'}]
    elif operation == 'conflict':
        original = {x['id']: x for x in payload['items']}
        result['detected'] = [
            {'id': x['id'], 'requested': original[x['id']]['preferred'],
             'proposal': x['start'], 'constraints_rechecked': True}
            for x in result['schedule'] if x['changed_time']]
        result['detected'] += [{'id': x['id'], 'blocked': True, 'reason': x['reason']} for x in result['unscheduled']]
        result['existing_reservations_moved'] = False
    else:
        result['load_minutes'] = [
            {'technician': x['id'], 'minutes': sum(minute(r['end']) - minute(r['start'])
                for r in result['schedule'] if r['technician'] == x['id'])}
            for x in payload['technicians']]
    return result
