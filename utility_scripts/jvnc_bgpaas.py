#!/usr/bin/env python

import sys
sys.path.append('/opt/avi/python/bin/cloud_connector')

import json
import logging
import requests
import avi.util.openstack_utils as osutils
import lib.cloud_connector_constants as const
import neutronclient.neutron.client as nnclient

from avi.infrastructure import datastore
from avi.util.jvnc_utils import VNCJ_DEFAULT_HEADERS
from avi.util.jvnc_utils import get_auth_hdrs, get_vnc_url
from avi.util.net_util import verify_connectivity
from avi.util.openstack_utils import ks_ip_port
from avi.util.os_utils2 import ks_clients, ksc_isv2, ks_splitnd, DEF_DOM_ID
from lib.cc_rest_utils import do_rest_api


root = logging.getLogger()
root.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')  # noqa

fh = logging.FileHandler('jvnc_bgpaas.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
root.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.ERROR)
ch.setFormatter(formatter)
root.addHandler(ch)

log = logging.getLogger()
ds = datastore.Datastore()
ksc = None
# IMPORTANT: UPDATE THIS
bgpsid = 'my-bgp-as-a-service-uuid'
ccfg = None


ENDPOINTS = ['compute', 'network', 'image', 'identity']
EVENT_ID = 'event_id'
EVENT_DETAILS = 'event_details'
EVENT_OBJ_UUID = 'obj_uuid'
VMI = 'virtual-machine-interface'
BGPAAS = 'bgp-as-a-service'


def _wrap_api(session, method, *args, **kwargs):
    global ksc
    xhdrs = get_auth_hdrs(ksc)
    if 'headers' not in kwargs:
        kwargs['headers'] = xhdrs
    else:
        kwargs['headers'].update(xhdrs)

    obj = do_rest_api(log, session, method, *args, **kwargs)
    return obj


def _vnc_get_bgpaas(oscfg):
    global bgpsid
    refapi = get_vnc_url(oscfg.contrail_endpoint)
    session = requests.session()
    session.headers.update(VNCJ_DEFAULT_HEADERS)
    rsrc = '/'.join([refapi, BGPAAS, bgpsid])
    bgp = _wrap_api(session, 'get', rsrc)[BGPAAS]
    return bgp


def _vnc_put_bgpaas(oscfg, bgp, add_vmis=[], del_vmids=[]):
    # Add VMI ref to list of VMI refs
    c_refs = bgp.get('virtual_machine_interface_refs', [])
    c_ref_ids = {i['uuid'] for i in c_refs}
    if add_vmis:
        for vmi in add_vmis:
            if vmi['uuid'] not in c_ref_ids:
                ref = {'to': vmi['fq_name'], 'uuid': vmi['uuid'],
                       'href': vmi['href'], 'attr': None}
                c_refs.append(ref)

    if del_vmids:
        c_refs = [i for i in c_refs if i['uuid'] not in del_vmids]

    bgp['virtual_machine_interface_refs'] = c_refs
    refapi = get_vnc_url(oscfg.contrail_endpoint)
    session = requests.session()
    session.headers.update(VNCJ_DEFAULT_HEADERS)
    body = {BGPAAS: bgp}
    rsrc = '/'.join([refapi, BGPAAS, bgp['uuid']])
    _wrap_api(session, 'put', rsrc, data=json.dumps(body))


def _vnc_get_vmi_iip(oscfg, portid, ip=None):
    refapi = get_vnc_url(oscfg.contrail_endpoint)
    session = requests.session()
    session.headers.update(VNCJ_DEFAULT_HEADERS)
    rsrc = '/'.join([refapi, VMI, portid])
    pvmi = _wrap_api(session, 'get', rsrc)[VMI]
    return pvmi


def get_endpoints(tksc, service=None, eptype=None, region=None):
    aref = tksc.session.auth.auth_ref
    eps = aref.service_catalog.get_endpoints(service_type=service,
                                             region_name=region,
                                             endpoint_type=eptype)
    return eps


def os_try_access(username, password, tenant, ip, timeout=5,
                  region=None, auth_url=None, log=None):
    (kip, kport, auth_url) = ks_ip_port(ip, auth_url=auth_url)
    rc, msg = verify_connectivity(kip, kport)
    if not rc:
        return (None, msg)

    skwopts = {'timeout': 15, 'verify': False}
    ckwopts = {'connect_retries': const.CONNECT_RETRIES,
               'region_name': region}
    try:
        rc, uksc, tksc = ks_clients(auth_url, username, password,
                                    pd_name=tenant, skwopts=skwopts,
                                    ckwopts=ckwopts, logger=log)
        eps = get_endpoints(tksc, eptype='public', region=region)
        for ep in ENDPOINTS:
            if not eps.get(ep):
                msg = ('Endpoint for %s not found in region %s (possibly bad region)' % (ep, region))  # noqa
                return (None, msg)
    except Exception as e:
        msg = str(e)
        return (None, msg)

    return (tksc, 'Success')


def fetch_oscfg():
    global ccfg
    if ccfg:
        return ccfg.uuid, ccfg.openstack_configuration

    clouds = ds.get_all('cloud')
    for c in clouds:
        cfg = c['config']
        if cfg.HasField('openstack_configuration'):
            ccfg = cfg
            return cfg.uuid, cfg.openstack_configuration

    return None, None


def get_tneuc(tname, auth_url, oscfg):
    ksc, err = os_try_access(oscfg.username, oscfg.password,
                             tname, oscfg.keystone_host,
                             const.REQUEST_TIMEOUT, oscfg.region,
                             auth_url=auth_url, log=log)
    if not ksc:
        log.error("Cloudn't establish connection to OpenStack, error: "
                  "%s", err)
        sys.exit(1)

    eptype = 'internal' if oscfg.use_internal_endpoints else 'public'
    ckwopts = {'connect_retries': const.CONNECT_RETRIES,
               'region_name': oscfg.region, 'interface': eptype}
    tneuc = nnclient.Client('2', session=ksc.session,
                            retries=const.API_RETRIES, **ckwopts)
    return tneuc, ksc


def get_neuc(tname):
    global ksc
    _, oscfg = fetch_oscfg()
    if not oscfg:
        log.error("OpenStack Configuration not found!")
        sys.exit(1)

    (_, _, auth_url) = osutils.ks_ip_port(oscfg.keystone_host,
                                          auth_url=oscfg.auth_url)
    aneuc, ksc = get_tneuc(oscfg.admin_tenant, auth_url, oscfg)
    if tname == oscfg.admin_tenant:
        tneuc = aneuc
    else:
        tneuc, _ = get_tneuc(tname, auth_url, oscfg)

    return aneuc, tneuc


def add_vmi_to_bgpaas(pid):
    print "Adding port %s" % pid
    _, oscfg = fetch_oscfg()
    bgp = _vnc_get_bgpaas(oscfg)
    if not bgp:
        print "Cloudn't find bgp service"
        sys.exit(1)

    vmi = _vnc_get_vmi_iip(oscfg, pid)
    if not vmi:
        print "Cloudn't find VMI for port id %s" % pid
        sys.exit(1)

    _vnc_put_bgpaas(oscfg, bgp, add_vmis=[vmi, ])


def remove_vmis_from_bgpaas(pids):
    log.info("Removing ports %s" % pids)
    _, oscfg = fetch_oscfg()
    bgp = _vnc_get_bgpaas(oscfg)
    if not bgp:
        print "Cloudn't find bgp service"
        sys.exit(1)

    vmids = pids
    if not vmids:
        log.error("Couldn't find vmis")
        sys.exit(1)

    _vnc_put_bgpaas(oscfg, bgp, del_vmids=vmids)


def handle_cc_ip_attached(ev, ouuid):
    ip = ev['cc_ip_details']['ip']['addr']
    sevm_uuid = ev['cc_ip_details']['se_vm_uuid']
    pid = ev['cc_ip_details']['port_uuid']
    print "attach_ip %s, on SE %s pid %s" % (ip, sevm_uuid, pid)
    if not pid:
        log.error("Cound't get the attach_ip port id from event." +
                  " (May be non-ECMP scaleout to secondary)")
        sys.exit(1)

    se = ds.get('serviceengine', uuid=sevm_uuid)
    if not se:
        log.error("SE %s doesn't exist in datastore", sevm_uuid)
        sys.exit(1)

    t = ds.get('tenant', uuid=se['config'].tenant_uuid)
    aneuc, tneuc = get_neuc(t['config'].name)
    add_vmi_to_bgpaas(pid)


def handle_cc_ip_detached(ev, ouuid):
    ip = ev['cc_ip_details']['ip']['addr']
    sevm_uuid = ev['cc_ip_details']['se_vm_uuid']
    pids = ev['cc_ip_details']['port_uuid']
    if not pids:
        log.error("Couldn't get detach_ip SE port ID (May be " +
                  "non-ECMP scalein from secondary)")
        sys.exit(1)

    pids = pids.split(',')
    log.info("detach_ip %s, on SE %s, pids %s", ip, sevm_uuid, pids)
    se = ds.get('serviceengine', uuid=sevm_uuid)
    if not se:
        log.error("SE %s doesn't exist in datastore", sevm_uuid)
        sys.exit(1)
    t = ds.get('tenant', uuid=se['config'].tenant_uuid)
    aneuc, tneuc = get_neuc(t['config'].name)
    _, oscfg = fetch_oscfg()
    neuc = tneuc if oscfg.tenant_se else aneuc
    ports = neuc.list_ports(id=pids)['ports']
    # Filter out Avi-Data ports hosting VIPs
    hosting_pids = {p['id'] for p in ports
                    if 'Avi-Data' in p.get('name', '')
                    and len(p.get('fixed_ips', [])) > 1}
    pids = [pid for pid in pids if pid not in hosting_pids]
    remove_vmis_from_bgpaas(pids)


def handle(ev):
    eid = ev.get(EVENT_ID, '')
    if eid not in ['CC_IP_ATTACHED', 'CC_IP_DETACHED']:
        log.info("Not handling event %s", eid)
        sys.exit(1)

    try:
        func = getattr(sys.modules[__name__], "handle_%s" % eid.lower())
        func(ev.get(EVENT_DETAILS, ''), ev.get(EVENT_OBJ_UUID, ''))
    except AttributeError:
        log.error("Couldn't find handler for %s", eid)
        raise


def main():
    ev_data = None
    try:
        ev_data = json.loads(sys.argv[1])
    except Exception as e:
        log.error("Failed to load event info: %s", e)
        sys.exit(1)

    if not ev_data:
        log.error("Event data missing!")
        sys.exit(1)

    log.debug('Recieved event: %s', ev_data)
    events = ev_data.get('events', [])
    for ev in events:
        try:
            handle(ev)
        except Exception as e:
            log.error("Failed to handle event %s : %s",
                      ev.get('event_id'), e)


if __name__ == '__main__':
    main()
