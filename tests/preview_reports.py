"""Local visual QA only. Synthetic data is conspicuously labelled in the page."""
from datetime import datetime,timedelta,timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
import accounting as a
import usage_service as u
import wan_health as w

CONFIG={'source_id':'synthetic-preview','vdom':'root','accounting_timezone':'UTC','expected_devid':'FG-FIXTURE',
    'confirmed_wan_interfaces':['uplink-a','uplink-b'],'loki_receive_timestamps':True}
INVENTORY={'collected_at':'2026-01-01T00:00:00Z','interfaces':[
    {'name':'staff','vdom':'root','type':'vlan','role':'lan','parent':'port1','vlan_id':100,'issues':[]},
    {'name':'guests','vdom':'root','type':'vlan','role':'lan','parent':'port1','vlan_id':200,'issues':[]}]}
store=a.Store(':memory:',CONFIG,INVENTORY)
now=datetime.now(timezone.utc)
records=[]
for day in (0,1,2,5,9,20,40):
    at=(now-timedelta(days=day,minutes=10)).isoformat()
    for index,name in enumerate(('Design workstation','Build server','Guest laptop','Office tablet'),1):
        records.append({'timestamp':at,'fields':{'devid':'FG-FIXTURE','vd':'root','logid':'0000000020','type':'traffic','subtype':'forward',
            'sessionid':str(day*10+index),'srcip':'192.0.2.'+str(index),'srcport':'5000','dstip':'198.51.100.1','dstport':'443','proto':'6',
            'srcintf':'staff' if index<3 else 'guests','dstintf':'uplink-a','srcintfrole':'lan','dstintfrole':'wan','srcname':name,
            'mastersrcmac':'02:00:00:00:00:0'+str(index) if index!=4 else '', 'duration':'60',
            'sentdelta':str(920000000//index),'rcvddelta':str(12400000000//index)}})
store.ingest(records,checkpoint=int(time.time()*a.NS))
engine=u.Engine(store);engine.last_poll_ok=True;engine.last_attempt=time.time()
def wan(seconds=86400):
    stamp=time.time();first=stamp-seconds
    samples=[{'timestamp':first+i*60,'state':'DOWN' if 15<=i<23 else 'UP','latency_ms':210 if 35<=i<40 else 24,
        'packet_loss_pct':0} for i in range(int(seconds/60)) if not 50<=i<60]
    history=w.summarize(samples,first,stamp)
    return {'status':'OBSERVED','observed_at':stamp,'route':w.route_evidence({'status':'success','results':{'success':True,'interface':'uplink-a'}},['uplink-a'],'1.1.1.1'),
        'interfaces':[{'interface':'uplink-a','internet_state':'UP','physical_state':'UP','download_mbps':37.4,'upload_mbps':8.2,
        'latency_ms':24,'jitter_ms':1.6,'packet_loss_pct':0,'history':history},
        {'interface':'uplink-b','internet_state':'NO DATA','physical_state':'NO DATA','download_mbps':None,'upload_mbps':None,
        'latency_ms':None,'jitter_ms':None,'packet_loss_pct':None,'history':w.summarize([],first,stamp)}]}
engine.wan=wan
with tempfile.TemporaryDirectory(prefix='fortilens-preview-') as directory:
    html=(ROOT/'portable/reporting.html').read_text(encoding='utf-8')
    html=html.replace('<body>','<body><div style="padding:9px 18px;background:#59471d;color:#fff0be;text-align:center;font-weight:600">SYNTHETIC PREVIEW · Test data only · Not a live company network</div>')
    Path(directory,'reporting.html').write_text(html,encoding='utf-8')
    server=ThreadingHTTPServer(('127.0.0.1',18767),u.handler(engine,'synthetic-preview-only',directory))
    print('Synthetic reports preview on http://127.0.0.1:18767/ (admin / synthetic-preview-only)',flush=True)
    try:server.serve_forever()
    finally:server.server_close();store.close()
