import unittest
from copy import deepcopy
from server.appointment_waitlist import rotate,receipt


def sample():
    return {'store_id':'SYN-STORE','slot':{'id':'SYN-SLOT','service':'massage','start':'2026-11-11T15:00:00+08:00','end':'2026-11-11T16:00:00+08:00'},
        'now':'2026-11-11T10:00:00+08:00','ledger':[],'window':['09:00','21:30'],'daily_cap':2,'contact_history':[],
        'candidates':[{'customer_id':'SYN-C1','services':['massage'],'available_from':'2026-11-11T14:00:00+08:00','available_to':'2026-11-11T17:00:00+08:00','rotation_order':1},
                      {'customer_id':'SYN-C2','services':['massage'],'available_from':'2026-11-11T14:00:00+08:00','available_to':'2026-11-11T17:00:00+08:00','rotation_order':2}]}


class Rotation(unittest.TestCase):
    def test_single_candidate(self):
        result=rotate(sample());self.assertEqual(result['proposal']['customer_id'],'SYN-C1')
        self.assertEqual(len(result['ledger']),1);self.assertFalse(result['actually_sent'])
    def test_draft_does_not_start_timeout(self):
        data=sample();data['ledger']=rotate(data)['ledger'];data['now']='2026-11-11T11:00:00+08:00'
        self.assertEqual(rotate(data)['decision'],'await_owner_and_front_desk')
    def test_wait29min59(self):
        data=sample();data['ledger']=receipt(rotate(data)['ledger'],'SYN-C1','sent',data['now']);data['now']='2026-11-11T10:29:59+08:00'
        self.assertEqual(rotate(data)['decision'],'await_customer')
    def test_exact30min_advances_once(self):
        data=sample();data['ledger']=receipt(rotate(data)['ledger'],'SYN-C1','sent',data['now']);data['now']='2026-11-11T10:30:00+08:00'
        result=rotate(data);self.assertEqual(result['proposal']['customer_id'],'SYN-C2');self.assertEqual(result['ledger'][0]['state'],'expired')
        data['ledger']=result['ledger'];self.assertEqual(rotate(data)['proposal']['customer_id'],'SYN-C2')
    def test_confirmation_stops_rotation(self):
        data=sample();ledger=receipt(rotate(data)['ledger'],'SYN-C1','sent',data['now']);data['ledger']=receipt(ledger,'SYN-C1','confirmed','2026-11-11T10:01:00+08:00')
        self.assertEqual(rotate(data)['decision'],'slot_filled')
    def test_reply_cannot_precede_sending(self):
        data=sample()
        with self.assertRaises(ValueError):receipt(rotate(data)['ledger'],'SYN-C1','confirmed',data['now'])
    def test_decline_advances(self):
        data=sample();ledger=receipt(rotate(data)['ledger'],'SYN-C1','sent',data['now']);data['ledger']=receipt(ledger,'SYN-C1','declined','2026-11-11T10:01:00+08:00')
        self.assertEqual(rotate(data)['proposal']['customer_id'],'SYN-C2')
    def test_cross_flow_frequency(self):
        data=sample();data['contact_history']=[{'id':str(i),'store_id':'SYN-STORE','customer_id':'SYN-C1','at':data['now'],'state':'reserved','flow':'other'}for i in range(2)]
        self.assertEqual(rotate(data)['proposal']['customer_id'],'SYN-C2')
    def test_history_deduplication(self):
        data=sample();h={'id':'same','store_id':'SYN-STORE','customer_id':'SYN-C1','at':data['now'],'state':'sent'};data['contact_history']=[h,h]
        self.assertEqual(rotate(data)['proposal']['customer_id'],'SYN-C1')
    def test_store_isolation(self):
        data=sample();data['ledger']=rotate(data)['ledger'];data['ledger'][0]['store_id']='OTHER'
        with self.assertRaises(ValueError):rotate(data)
    def test_quiet_and_closed(self):
        data=sample();data['now']='2026-11-11T08:59:00+08:00';self.assertEqual(rotate(data)['decision'],'quiet_window')
        data['now']=data['slot']['start'];self.assertEqual(rotate(data)['decision'],'slot_closed')
    def test_service_and_full_slot_availability(self):
        data=sample();data['candidates'][0]['available_to']='2026-11-11T15:30:00+08:00'
        self.assertEqual(rotate(data)['proposal']['customer_id'],'SYN-C2')
    def test_no_input_mutation(self):
        data=sample();before=deepcopy(data);rotate(data);self.assertEqual(data,before)
    def test_missing(self):self.assertEqual(rotate({})['status'],'needs_input')


if __name__=='__main__':unittest.main()
