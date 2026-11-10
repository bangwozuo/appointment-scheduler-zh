import unittest
from copy import deepcopy
from server.appointment_notification import notification

def sample():
    return {'items':[{'id':'SYN-N1','customer_id':'SYN-C1','appointment_at':'2026-11-12T18:00:00+08:00','before_minutes':1440,
                     'service':'SPA','technician':'T1','change_route':'contact store','wechat_followed':True,'wechat_eligibility_verified':True,'no_show_count':2}],
            'history':[],'window':['09:00','21:30'],'daily_cap':2,'store_address':'SYN-ADDRESS','now':'2026-11-11T08:00:00+08:00'}

class CompleteNotification(unittest.TestCase):
    def test_previous_day_window_instead_of_24h(self):
        r=notification(sample());self.assertEqual(r['schedule'][0]['at'],'2026-11-11T15:00:00+08:00');self.assertFalse(r['actually_sent'])
    def test_today_time_within_previous_day_window(self):
        d=sample();d['now']='2026-11-11T15:30:00+08:00';self.assertEqual(notification(d)['schedule'][0]['at'],d['now'])
    def test_missed_previous_day_window_is_not_silently_rescheduled(self):
        d=sample();d['now']='2026-11-11T16:01:00+08:00';r=notification(d);self.assertEqual(r['schedule'],[]);self.assertEqual(r['unscheduled'][0]['reason'],'previous_day_window_missed')
    def test_30min_and_two_hour_trigger(self):
        for offset,expected in [(30,'17:30'),(120,'16:00')]:
            d=sample();d['now']='2026-11-12T10:00:00+08:00';d['items'][0]['before_minutes']=offset
            self.assertIn(expected,notification(d)['schedule'][0]['at'])
    def test_quiet_hours_before_appointment(self):
        d=sample();d['items'][0].update(appointment_at='2026-11-11T10:00:00+08:00',before_minutes=120)
        self.assertEqual(notification(d)['schedule'][0]['at'],'2026-11-11T09:00:00+08:00')
    def test_cross_flow_reserved_draft_counts_toward_cap(self):
        d=sample();d['daily_cap']=1;d['history']=[{'id':'OTHER-FLOW','customer_id':'SYN-C1','at':'2026-11-11T10:00:00+08:00','state':'reserved'}]
        self.assertEqual(notification(d)['unscheduled'][0]['reason'],'daily_frequency_cap')
    def test_same_day_merge_and_original_ids(self):
        d=sample();d['daily_cap']=1;other=deepcopy(d['items'][0]);other['id']='SYN-N2';d['items'].append(other)
        r=notification(d);self.assertEqual(len(r['schedule']),1);self.assertEqual(r['schedule'][0]['source_ids'],['SYN-N1','SYN-N2'])
    def test_missing_input_and_unverified_channel(self):
        self.assertEqual(notification({})['status'],'needs_input')
        d=sample();d['items'][0]['wechat_eligibility_verified']=False
        self.assertEqual(notification(d)['schedule'][0]['channel'],'sms_unverified_draft')
    def test_identical_historical_trigger_not_replayed(self):
        d=sample();d['history']=[{'id':'SYN-N1','customer_id':'SYN-C1','at':'2026-11-11T15:00:00+08:00'}]
        self.assertEqual(notification(d)['schedule'],[])
    def test_timezone_and_day_window_limits(self):
        d=sample();d['previous_day_window']=['22:00','23:00']
        with self.assertRaises(ValueError):notification(d)
        d=sample();d['now']='2026-11-11T08:00:00'
        with self.assertRaises(ValueError):notification(d)

if __name__=='__main__':unittest.main()
