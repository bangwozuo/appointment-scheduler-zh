from pathlib import Path
import csv,json
from server.appointment_shared_flows import shared_flow
def execute(payload,context=None):
 if set(payload)!={'operation','data','data_origin'} or payload['data_origin']!='SYNTHETIC':raise ValueError('Exact synthetic source required')
 result=shared_flow(payload['operation'],payload['data'])
 result.update(data_origin='SYNTHETIC',actually_published=False)
 if result['status']=='needs_input':return {**result,'output_files':[]}
 dest=Path(__file__).resolve().parent.parent/'dist';dest.mkdir(exist_ok=True)
 (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),'utf-8')
 with (dest/'schedule.csv').open('w',encoding='utf-8-sig',newline='')as f:
  w=csv.writer(f);w.writerow(['预约单','开始','结束','技师','房间','设备','确认状态'])
  for row in result['schedule']:w.writerow([row['id'],row['start'],row['end'],row['technician'],row['room'],','.join(row['equipment']),row['approval']])
 return {**result,'output_files':['result.json','schedule.csv']}
