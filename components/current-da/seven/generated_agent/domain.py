import json,subprocess,sys,shutil
from pathlib import Path
OPS={'commission': 'skills/commission-calc-assist/scripts/commission_calc.py', 'forecast': 'skills/traffic-demand-forecast/scripts/demand_forecast.py', 'conflict': 'workflows/conflict-oversell-detect-flow/scripts/run_flow.py', 'no_show': 'workflows/no-show-predict-backfill-flow/scripts/run_flow.py', 'booking': 'workflows/online-booking-intake-flow/scripts/run_flow.py', 'weekly': 'workflows/scheduling-efficiency-weekly-flow/scripts/run_flow.py', 'scheduling': 'workflows/staff-room-scheduling-flow/scripts/run_flow.py'}
def execute(payload,context=None):
    if set(payload)!={'operation','data','data_origin'} or payload['operation'] not in OPS or payload['data_origin']!='SYNTHETIC':raise ValueError('Unregistered operation or source boundary')
    data=payload['data'];op=payload['operation']
    if not isinstance(data,dict):raise ValueError('data must be an object')
    required={'commission':['items','rules'],'forecast':['series','trend_pct','band_pct','horizon_days'],'conflict':['items','technicians','rooms','existing','incoming','last_start_min','close_min'],'no_show':['bookings','baseline_pct','waitlist','quiet_window','daily_cap'],'booking':['pricebook','technicians','rooms','existing','request','close_min','last_start_min','buffer_min'],'weekly':['buckets','week_total_trend_pct','weekday_cap','weekend_cap','quiet_window','daily_cap'],'scheduling':['items','technicians','rooms','bookings','last_start_min','close_min']}[op]
    missing=[k for k in required if k not in data]
    if missing:return {'status':'needs_input','missing_fields':missing,'data_origin':'SYNTHETIC','output_files':[],'actually_published':False}
    if op=='commission':
        if not data['items']:return {'status':'needs_input','missing_fields':['items'],'data_origin':'SYNTHETIC','output_files':[],'actually_published':False}
        rules=data['rules'];rates=rules.get('category_rates',{})
        missing=['rules.category_rates.'+str(x.get('category')) for x in data['items']if x.get('category') not in rates]
        if 'channel_deduction' not in rules:missing.append('rules.channel_deduction')
        if data.get('stored_card')and 'card_rate' not in rules:missing.append('rules.card_rate')
        if missing:return {'status':'needs_input','missing_fields':missing,'data_origin':'SYNTHETIC','output_files':[],'actually_published':False}
        if rules.get('round',2)!=2:raise ValueError('This contract requires two decimals')
        for x in data['items']:
            if type(x.get('count')) is not int or x['count']<0 or type(x.get('unit_price'))not in {int,float} or x['unit_price']<0:raise ValueError('Invalid quantity or unit price')
        if any(type(v)not in {int,float}or not 0<=v<=1 for v in [*rates.values(),*rules['channel_deduction'].values(),rules.get('card_rate',0)]):raise ValueError('Invalid rate')
    root=Path(__file__).resolve().parent.parent;dest=root/'dist';dest.mkdir(exist_ok=True)
    inputfile=root/'business-input.json';inputfile.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),'utf-8')
    done=subprocess.run([sys.executable,str(root/'repo'/OPS[op]),'--input',str(inputfile),'--outdir',str(dest)],cwd=root,capture_output=True,text=True,timeout=45)
    if done.returncode:raise ValueError('Original script stopped: '+done.stderr[-1800:])
    # Keep original stdout, distinguish a draft from any external execution.
    (dest/'script-stdout.txt').write_text(done.stdout,'utf-8')
    result={'status':'draft','operation':op,'data_origin':'SYNTHETIC','original_source_script':OPS[op],'actually_published':False,'output_files':sorted(p.name for p in dest.iterdir()if p.is_file())}
    (dest/'source-execution.json').write_text(json.dumps(result,ensure_ascii=False),'utf-8');result['output_files'].append('source-execution.json');return result
