from pathlib import Path
import csv,json
from server.appointment_rules import schedule,notification
def execute(payload,context=None):
 if set(payload)!={'operation','data','data_origin'} or payload['data_origin']!='SYNTHETIC':raise ValueError('Exact SYNTHETIC contract required')
 op=payload['operation']
 if op not in {'smart','notify'}:raise ValueError('Unknown capability')
 result=(schedule if op=='smart' else notification)(payload['data'])
 result.update(data_origin='SYNTHETIC',operation=op,actually_published=False)
 if result['status']=='needs_input':return {**result,'output_files':[]}
 dest=Path(__file__).resolve().parent.parent/'dist';dest.mkdir(exist_ok=True)
 (dest/'result.json').write_text(json.dumps(result,ensure_ascii=False,allow_nan=False),'utf-8')
 with (dest/'schedule.csv').open('w',encoding='utf-8-sig',newline='')as stream:
  writer=csv.writer(stream);writer.writerow(['source_id','resource_or_channel','proposed_time','confirmation'])
  for row in result['schedule']:
   writer.writerow([row.get('id',','.join(row.get('source_ids',[]))),row.get('technician',row.get('channel')),row.get('start',row.get('at')),row['approval']])
 return {**result,'output_files':['result.json','schedule.csv']}
