import logging
import sys
import lib.cloud_connector_constants as const
import avi.util.openstack_utils as osutils
import neutronclient.neutron.client as nnclient

from avi.infrastructure import datastore
from openstack.os_utils import os_try_access


root = logging.getLogger()
root.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')  # noqa

fh = logging.FileHandler('fix_aap.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
root.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.DEBUG)
ch.setFormatter(formatter)
root.addHandler(ch)

log = logging.getLogger()


def fetch_oscfg(ds):
    clouds = ds.get_all('cloud')
    for c in clouds:
        cfg = c['config']
        if cfg.HasField('openstack_configuration'):
            return cfg.uuid, cfg.openstack_configuration

    return None, None


def main():
    ds = datastore.Datastore()
    cuuid, oscfg = fetch_oscfg(ds)
    if not oscfg:
        log.error("OpenStack Configuration not found!")
        sys.exit(1)

    (kip, kport, auth_url) = osutils.ks_ip_port(oscfg.keystone_host,
                                                auth_url=oscfg.auth_url)
    ksc, err = os_try_access(oscfg.username, oscfg.password,
                             oscfg.admin_tenant, oscfg.keystone_host,
                             const.REQUEST_TIMEOUT, oscfg.region,
                             auth_url=auth_url, log=log)
    if not ksc:
        log.error("Cloudn't establish connection to OpenStack, error: "
                  "%s", err)
        sys.exit(1)

    eptype = 'internal' if oscfg.use_internal_endpoints else 'public'
    ckwopts = {'connect_retries': const.CONNECT_RETRIES,
               'region_name': oscfg.region, 'interface': eptype}
    neuc = nnclient.Client('2', session=ksc.session,
                           retries=const.API_RETRIES, **ckwopts)

    # Get all SEs from DB
    # For each SE, get all ports using device_id field,
    # and update the AAP entry if it has it.
    ses = ds.get_all('serviceengine')
    for se in ses:
        secfg = se['config']
        if secfg.cloud_uuid != cuuid:
            continue

        devid = secfg.uuid.split('se-')[1]
        log.info("Updating ports for se %s, %s", secfg.name, devid)
        data = neuc.list_ports(device_id=devid)
        ports = data.get('ports', [])
        for port in ports:
            aaps = port.get('allowed_address_pairs', [])
            if not aaps:
                continue

            new_aaps = []
            for aap in aaps:
                tokens = aap['ip_address'].split('/')
                if len(tokens) < 2:
                    continue

                ip = tokens[0] + '/%s' % '32'
                naap = {'ip_address': ip}
                if aap.get('mac_address', None):
                    naap['mac_address'] = aap['mac_address']

                new_aaps.append(naap)

            if new_aaps:
                body = {'port': {'allowed_address_pairs': new_aaps}}
                neuc.update_port(port=port['id'], body=body)
                log.info("Updated port %s with new AAP %s", port['name'],
                         new_aaps)


if __name__ == '__main__':
    main()
