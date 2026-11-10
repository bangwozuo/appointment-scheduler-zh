from pathlib import Path
import json,csv
from server.appointment_waitlist import rotate
def execute(payload,context=None):
 if set(payload)!={'operation','data','data_origin'}or payload['operation']!='rotation'or payload['data_origin']!='SYNTHETIC':raise ValueError('Wrong source or operation')
 result=rotate(payload['data']);result.update(data_origin='SYNTHETIC',actually_published=False)
 if result['status']=='needs_input':return {**result,'output_files':[]}
 dest=Path(__file__).resolve().parent.parent/'dist';dest.mkdir(exist_ok=True)
 (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),'utf-8')
 with(dest/'waitlist.csv').open('w',encoding='utf-8-sig',newline='')as f:
  w=csv.writer(f);w.writerow(['门店','空位','客户','状态','草稿时间','实际通知回执时间'])
  for r in result['ledger']:w.writerow([r['store_id'],r['slot_id'],r['customer_id'],r['state'],r.get('proposed_at',''),r.get('sent_at','')])
 return {**result,'output_files':['result.json','waitlist.csv']}
