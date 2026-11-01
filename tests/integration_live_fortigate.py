"""Optional, read-only real-device compatibility check. Never runs in normal CI."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from management.common import Problem,setup
from management.models import Settings
from management.runtime import fortigate
import interfaces


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True,help='Existing private site.json')
    parser.add_argument('--token-file',type=Path,required=True,help='Existing plain API-token file, never a command-line token')
    parser.add_argument('--root',type=Path,help='Installation root for relative CA paths; defaults to the config directory')
    args=parser.parse_args()
    try:
        c=setup.validate_config(setup.load(args.config))
        if not c['api_tls_verify']:
            raise Problem('tls_required','Use a verified FortiGate certificate/custom CA for this optional compatibility test.')
        with args.token_file.open(encoding='utf-8') as stream:token=stream.read(4097).strip()
        if not token or len(token)>4096 or any(ord(x)<33 or ord(x)>126 for x in token):
            raise Problem('token_file_invalid','Choose the existing plain token file; its contents are never printed.')
        ca=None
        if c['api_ca_file']:
            ca_path=Path(c['api_ca_file'])
            if not ca_path.is_absolute():ca_path=(args.root or args.config.parent)/ca_path
            with ca_path.open(encoding='utf-8') as stream:ca=stream.read(2097153)
            if len(ca)>2097152:raise Problem('ca_limit','The custom CA exceeds the supported size limit.')
        s=Settings(fortigate_host=c['fortigate_host'],api_port=c['api_port'],vdom=c['vdom'],api_tls_verify=True)
        data=fortigate(s,token,'/api/v2/monitor/system/status',{'vdom':c['vdom']},ca)
        if not isinstance(data,dict) or data.get('status')!='success':
            raise Problem('api_status_unconfirmed','The API did not confirm a successful system/status read.')
        details=data.get('results') if isinstance(data.get('results'),dict) else {}
        serial=data.get('serial',details.get('serial'))
        if c.get('expected_devid') and serial and serial!=c['expected_devid']:
            raise Problem('device_identity_mismatch','The configured source ID did not match this API device.')
        def getter(start):return fortigate(s,token,'/api/v2/cmdb/system/interface',{'vdom':c['vdom'],'start':start,'count':500,'format':'name|vdom|type|role|interface|vlanid'},ca)
        report=interfaces.collect(c,token,api_get=getter)
        if not report['coverage'].startswith('Accessible API response'):
            raise Problem('metadata_unavailable','The interface API was not confirmed. Review read permissions and VDOM scope.')
        if c.get('confirmed_wan_interfaces'):interfaces.selected_names(report,c['confirmed_wan_interfaces'],c['vdom'])
        partial='partial' in report['coverage']
        print(json.dumps({'result':'WARN' if partial else 'PASS','system_status_read':'PASS','tls_verified':True,
            'interface_count':len(report['interfaces']),'coverage':report['coverage'],
            'scope':'Connection/metadata only. WAN health, log completeness and numeric usage accuracy were not verified.'},indent=2))
        return 2 if partial else 0
    except (Problem,interfaces.InventoryError) as e:
        print(json.dumps({'result':'FAIL','code':e.code,'message':getattr(e,'message','Interface metadata could not be confirmed. Review VDOM, read permissions and compatibility.')}));return 1
    except Exception:
        print(json.dumps({'result':'FAIL','code':'private_input_or_compatibility_error','message':'Review private files, dependencies and device compatibility. Input and response contents are hidden.'}));return 1


if __name__=='__main__':raise SystemExit(main())
