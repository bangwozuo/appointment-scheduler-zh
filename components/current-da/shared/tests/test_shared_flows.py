import unittest
from copy import deepcopy
from unittest.mock import patch
from server.appointment_shared_flows import shared_flow


def sample():
    return {'store_id': 'SYN-STORE', 'snapshot_id': 'SYN-SNAPSHOT-1',
        'pricebook': {'massage': {'duration_min': 60, 'price': 100}},
        'resource_snapshot': {
            'open': '09:00', 'close': '21:00', 'last_start': '20:00', 'buffer_min': 15, 'step_min': 15,
            'technicians': [
                {'id': 'T1', 'services': ['massage'], 'shift': ['09:00', '18:00'], 'breaks': [['12:00', '13:00']]},
                {'id': 'T2', 'services': ['massage'], 'shift': ['09:00', '18:00'], 'breaks': [['13:00', '14:00']]}],
            'rooms': [{'id': 'R1', 'type': 'single'}, {'id': 'R2', 'type': 'single'}],
            'equipment': {'E1': 1},
            'existing': [{'id': 'SYN-OLD', 'start': '10:00', 'end': '11:00', 'technician': 'T1', 'room': 'R1', 'equipment': ['E1']}],
            'items': [{'id': 'SYN-NEW', 'service': 'massage', 'preferred': '10:00', 'earliest': '10:00',
                       'latest': '17:00', 'duration_min': 60, 'room_type': 'single', 'equipment': ['E1']}]}}


class SharedConsumers(unittest.TestCase):
    def test_three_consumers_call_actual_shared_algorithm(self):
        from server.appointment_rules import schedule
        for op in ('booking', 'scheduling', 'conflict'):
            with self.subTest(op=op), patch('server.appointment_shared_flows.schedule', wraps=schedule) as shared:
                result = shared_flow(op, sample())
                shared.assert_called_once()
                self.assertEqual(result['schedule'][0]['start'], '11:15')
                self.assertEqual(result['shared_capability'], 'smart-scheduling-engine')
                self.assertFalse(result['external_booking_created'])

    def test_intake_earliest_five_minutes(self):
        data = sample(); data['resource_snapshot']['existing'] = []
        data['resource_snapshot']['items'][0].update(earliest='10:05', preferred='16:00')
        result = shared_flow('booking', data)
        self.assertEqual(result['slot']['start'], '10:05')
        self.assertEqual(result['price'], 100)
        self.assertEqual([x['status'] for x in result['steps'][-2:]], ['pending', 'pending'])

    def test_price_missing_does_not_invent(self):
        data = sample(); data['pricebook'] = {}
        self.assertEqual(shared_flow('booking', data)['status'], 'needs_input')

    def test_duration_conflict_stops(self):
        data = sample(); data['pricebook']['massage']['duration_min'] = 45
        with self.assertRaises(ValueError): shared_flow('booking', data)

    def test_missing_resource_snapshot(self):
        self.assertEqual(shared_flow('scheduling', {})['status'], 'needs_input')

    def test_resource_snapshot_not_mutated(self):
        data = sample(); before = deepcopy(data)
        shared_flow('booking', data)
        self.assertEqual(data, before)

    def test_hard_equipment_constraint_shared(self):
        for op in ('booking', 'scheduling', 'conflict'):
            data = sample(); data['resource_snapshot']['items'][0]['latest'] = '10:30'
            result = shared_flow(op, data)
            self.assertFalse(result['schedule'])
            self.assertEqual(len(result['unscheduled']), 1)

    def test_named_technician_shift_shared(self):
        data = sample(); data['resource_snapshot']['items'][0].update(named_technician='T1', earliest='18:00', latest='19:00')
        self.assertFalse(shared_flow('booking', data)['accepted'])

    def test_break_rechecked(self):
        data = sample(); data['resource_snapshot']['existing'] = []
        data['resource_snapshot']['items'][0].update(named_technician='T1', preferred='11:30', earliest='11:30')
        self.assertEqual(shared_flow('booking', data)['slot']['start'], '13:00')

    def test_original_reservations_preserved(self):
        data = sample(); old = deepcopy(data['resource_snapshot']['existing'])
        result = shared_flow('conflict', data)
        self.assertEqual(result['detected'][0]['requested'], '10:00')
        self.assertTrue(result['detected'][0]['constraints_rechecked'])
        self.assertFalse(result['existing_reservations_moved'])
        self.assertEqual(data['resource_snapshot']['existing'], old)

    def test_work_minutes_use_actual_schedule(self):
        result = shared_flow('scheduling', sample())
        self.assertEqual(sum(x['minutes'] for x in result['load_minutes']), 60)

    def test_unknown_consumer(self):
        with self.assertRaises(ValueError): shared_flow('other', sample())


if __name__ == '__main__': unittest.main()
