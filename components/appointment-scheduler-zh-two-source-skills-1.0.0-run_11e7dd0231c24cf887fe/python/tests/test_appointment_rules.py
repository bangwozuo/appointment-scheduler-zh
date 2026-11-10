import unittest
from copy import deepcopy
from server.appointment_rules import schedule, notification


def schedule_input():
    return {'items': [
        {'id': 'SYN-A', 'service': 'SPA', 'named_technician': 'T1', 'preferred': '10:00', 'earliest': '10:00', 'latest': '12:00', 'duration_min': 60, 'room_type': 'SPA', 'equipment': ['E1']},
        {'id': 'SYN-B', 'service': 'SPA', 'named_technician': None, 'preferred': '10:00', 'earliest': '10:00', 'latest': '12:00', 'duration_min': 60, 'room_type': 'SPA', 'equipment': ['E1']},
        {'id': 'SYN-C', 'service': 'SPA', 'named_technician': 'T1', 'preferred': '17:00', 'earliest': '17:00', 'latest': '18:00', 'duration_min': 60, 'room_type': 'SPA', 'equipment': []}],
        'technicians': [
            {'id': 'T1', 'shift': ['10:00', '16:00'], 'breaks': [['12:00', '13:00']], 'services': ['SPA']},
            {'id': 'T2', 'shift': ['10:00', '20:00'], 'breaks': [['13:00', '14:00']], 'services': ['SPA']}],
        'rooms': [{'id': 'R1', 'type': 'SPA'}, {'id': 'R2', 'type': 'SPA'}],
        'equipment': {'E1': 1}, 'existing': [], 'open': '10:00', 'close': '20:00', 'last_start': '19:00', 'buffer_min': 15, 'step_min': 15}


def notification_input():
    items = [{'id': 'SYN-N1', 'customer_id': 'SYN-C1', 'appointment_at': '2026-11-12T10:00:00+08:00', 'before_minutes': 1440,
              'service': 'SPA', 'technician': 'T1', 'change_route': 'contact store', 'wechat_followed': True, 'wechat_eligibility_verified': True, 'no_show_count': 2}]
    return {'items': items, 'history': [], 'window': ['09:00', '21:30'], 'daily_cap': 2, 'store_address': 'SYN-ADDRESS', 'now': '2026-11-11T08:00:00+08:00'}


class AppointmentRules(unittest.TestCase):
    def test_device_exclusion_and_shift(self):
        value = schedule(schedule_input())
        self.assertEqual([(x['id'], x['start']) for x in value['schedule']], [('SYN-A', '10:00'), ('SYN-B', '11:15')])
        self.assertEqual(value['unscheduled'][0]['id'], 'SYN-C')
        self.assertTrue(value['conflicts'][0]['constraints_rechecked'])
        self.assertFalse(value['actually_published'])

    def test_device_capacity_two(self):
        data = schedule_input();data['equipment']['E1'] = 2
        self.assertEqual([x['start'] for x in schedule(data)['schedule']], ['10:00', '10:00'])

    def test_missing_configuration_does_not_invent_resources(self):
        data = schedule_input();del data['equipment']
        self.assertEqual(schedule(data)['missing_fields'], ['equipment'])

    def test_unknown_device_is_retained_as_unscheduled(self):
        data = schedule_input();data['equipment'] = {}
        result = schedule(data)
        self.assertEqual(len(result['unscheduled']), 3)
        self.assertEqual(result['unscheduled'][0]['missing'], ['E1'])

    def test_break_training_and_buffer(self):
        data = schedule_input();data['items'] = [data['items'][0]]
        data['technicians'][0]['breaks'] = [['10:30', '11:30']]
        self.assertEqual(schedule(data)['schedule'][0]['start'], '11:30')

    def test_duplicate_identity_and_unsafe_rules_rejected(self):
        data = schedule_input();data['items'].append(deepcopy(data['items'][0]))
        with self.assertRaises(ValueError): schedule(data)
        data = schedule_input();data['buffer_min'] = 0
        with self.assertRaises(ValueError): schedule(data)

    def test_notification_quiet_window_and_draft(self):
        data = notification_input();data['items'][0]['before_minutes'] = 120
        data['items'][0]['appointment_at'] = '2026-11-11T10:00:00+08:00'
        result = notification(data)
        self.assertEqual(result['schedule'][0]['at'], '2026-11-11T09:00:00+08:00')
        self.assertIn('回复1确认', result['schedule'][0]['messages'][0])
        self.assertFalse(result['actually_sent'])

    def test_quiet_shift_does_not_pass_appointment(self):
        data = notification_input();data['items'][0]['appointment_at'] = '2026-11-11T08:30:00+08:00'
        data['items'][0]['before_minutes'] = 30
        self.assertEqual(notification(data)['schedule'], [])

    def test_day_frequency_cap(self):
        data = notification_input();data['daily_cap'] = 1
        data['history'] = [{'id': 'OLD', 'customer_id': 'SYN-C1', 'at': '2026-11-11T09:00:00+08:00'}]
        self.assertEqual(notification(data)['unscheduled'][0]['reason'], 'daily_frequency_cap')

    def test_merge_does_not_exceed_cap(self):
        data = notification_input();data['daily_cap'] = 1
        second = deepcopy(data['items'][0]);second['id'] = 'SYN-N2';data['items'].append(second)
        result = notification(data)
        self.assertEqual(len(result['schedule']), 1)
        self.assertEqual(result['schedule'][0]['source_ids'], ['SYN-N1', 'SYN-N2'])

    def test_historical_notification_never_replayed(self):
        data = notification_input();data['history'] = [{'id': 'SYN-N1', 'customer_id': 'SYN-C1', 'at': '2026-11-11T09:00:00+08:00'}]
        self.assertEqual(notification(data)['unscheduled'][0]['reason'], 'already_recorded; no replay')

    def test_unverified_wechat_is_not_claimed_sendable(self):
        data = notification_input();data['items'][0]['wechat_eligibility_verified'] = False
        self.assertEqual(notification(data)['schedule'][0]['channel'], 'sms_unverified_draft')

    def test_timezone_and_window_boundaries(self):
        data = notification_input();data['now'] = '2026-11-11T08:00:00'
        with self.assertRaises(ValueError): notification(data)
        data = notification_input();data['window'][1] = '22:00'
        with self.assertRaises(ValueError): notification(data)


if __name__ == '__main__':
    unittest.main()
