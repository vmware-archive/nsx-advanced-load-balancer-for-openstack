import argparse
import json
import uuid
from avi.sdk.avi_api import ApiSession

parser = argparse.ArgumentParser()
parser.add_argument("--controller-ip", help="Avi Controller IP/VIP", required=True)
parser.add_argument("--password", help="Avi Controller password", required=True)
parser.add_argument("--tenant-uuid", help="OpenStack Tenant UUID",  required=True)
parser.add_argument("--tenant-name", help="OpenStack Tenant name",  required=True)
parser.add_argument("--se-in-provider-context", help="Service Engine in Provider Context",
                    action="store_true", default=False)
args = parser.parse_args()

api = ApiSession.get_session(args.controller_ip, 'admin', args.password)
tenant = {}
tuuid = uuid.UUID(args.tenant_uuid)
tenant['uuid'] = 'tenant-' + str(tuuid)
tenant['name'] = args.tenant_name
tenant['local'] = False
tenant['config_settings'] = {'se_in_provider_context': args.se_in_provider_context}
resp = api.post('/tenant', data=json.dumps(tenant), force_uuid=tenant['uuid'])
if 200 <= resp.status_code <= 299:
    print "Created Tenant %s" % tenant['name']
else:
    print "Failed to create tenant, error_code %s, error: %s" % (resp.status_code, resp.text)
