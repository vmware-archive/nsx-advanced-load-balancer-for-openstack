
############################################################################
#
# AVI CONFIDENTIAL
# __________________
#
# [2013] - [2018] Avi Networks Incorporated
# All Rights Reserved.
#
# NOTICE: All information contained herein is, and remains the property
# of Avi Networks Incorporated and its suppliers, if any. The intellectual
# and technical concepts contained herein are proprietary to Avi Networks
# Incorporated, and its suppliers and are covered by U.S. and Foreign
# Patents, patents in process, and are protected by trade secret or
# copyright law, and other laws. Dissemination of this information or
# reproduction of this material is strictly forbidden unless prior written
# permission is obtained from Avi Networks Incorporated.
###

import django
import os
import sys

sys.path.append('/opt/avi/python/bin/portal')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portal.settings_local')

django.setup()


import logging
import sys
from uuid import UUID as UUIDLibClass

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.urlresolvers import resolve

from keystoneclient.v2_0 import client as client_v2
from keystoneclient.v3 import client as client_v3
from api.models import Tenant
from api.models import UserActivity
from avi.util.cloud_util import get_openstack_config
from avi.rest.request_generator import RequestGenerator
from avi.util import openstack_utils as os_utils


logging.basicConfig()
root = logging.getLogger()
root.setLevel(logging.INFO)
# root.setLevel(logging.DEBUG)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')  # noqa

fh = logging.FileHandler('ks_cleanup.log')
# fh.setLevel(logging.INFO)
fh.setLevel(logging.DEBUG)
fh.setFormatter(formatter)
root.addHandler(fh)

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.WARNING)
#  ch.setLevel(logging.DEBUG)
ch.setFormatter(formatter)
root.addHandler(ch)

logger = logging.getLogger()


def get_accessible_tenants(os_conf, auth_url,
                           region_name=None, insecure=True, cacert=None,
                           timeout=5):
    """
    :param os_conf: openstack config
    :param auth_url: auth url
    :param region_name: region name
    :param insecure: insecure allowed for https
    :param cacert:
    :param timeout:
    :return: tenants, client
          list of tenants
          scoped or unscoped client -- v3 client
    """
    keystone_client = get_keystone_client(auth_url.replace("v2.0", "v3"))
    ks_args = {
        "password": os_conf.password,
        "auth_url": auth_url.replace("v2.0", "v3"),
        "region_name": region_name,
        "insecure": insecure,
        "cacert": cacert,
        "timeout": timeout,
        "debug": settings.DEBUG
    }
    (user, user_domain) = (os_conf.username, None)
    if get_keystone_version(auth_url) >= 3:
        (user, user_domain) = os_utils.getUserAndDomainFromConfig(os_conf)
        # keep the behavior similar to how access is done when configuring
        # via UI
        if not user_domain:
            user_domain = "Default"
    ks_args["username"] = user
    ks_args["user_domain_name"] = user_domain

    (tenant, tenant_domain) = os_utils.getTenantAndDomainFromConfig(os_conf)
    if get_keystone_version(auth_url) < 3:
        # ks_args["tenant_name"] = tenant
        ks_args["project_name"] = tenant
        ks_args["project_domain_name"] = None
    else:
        ks_args["project_name"] = tenant
        if tenant_domain:
            ks_args["project_domain_name"] = tenant_domain
        else:
            ks_args["project_domain_name"] = "Default"

    keystone_host = os_utils.getHostFromAuthURL(auth_url)

    # try with a scoped client first
    try:
        client = keystone_client.Client(**ks_args)
        os_utils.patchKeyStoneManagementURL(
            client, keystone_host,
            use_admin_url=os_conf.use_admin_url,
            logger=logger)
    except:  # noqa
        logger.exception("Failed in connecting to keystone "
                         "with configured credentials")
        return [], None

    # ensure that the configured user has access to all tenants
    #  of the user logging in
    ctenants = None
    try:
        ctenants = os_utils.getTenants(client, logger=logger,
                                       ks_args=ks_args.copy())
    except:  # noqa
        logger.warn("Could not get tenants list with scoped credentials")

    if ctenants:
        return ctenants, client

    # try unscoped now
    ks_args.pop("project_name", None)
    ks_args.pop("project_domain_name", None)
    ks_args.pop("tenant_name", None)
    try:
        tclient = keystone_client.Client(**ks_args)
        os_utils.patchKeyStoneManagementURL(
            tclient, keystone_host,
            use_admin_url=os_conf.use_admin_url,
            logger=logger)
        ctenants = os_utils.getTenants(tclient, logger=logger,
                                       ks_args=ks_args.copy())
    except Exception as e:
        logger.exception("Could not connect to openstack using "
                         "configured credentials: %s", e)
        ctenants = []

    # return tenant list and the scoped keystone client
    return ctenants, client


req_gen = None  # Request Generator
admin_user = None  # admin user for tenant creations and deletes


def internal_view_request(path, method_name, data=None, kwargs=None):
    global req_gen, admin_user
    if req_gen is None:
        req_gen = RequestGenerator()
    if not data:
        data = dict()
    if not kwargs:
        data = dict()
    if admin_user is None:
        admin_user = get_user_model().objects.get(username="admin")
    request = req_gen.create_request(path=path, method_name=method_name,
                                     data=data, user=admin_user)
    if data and "uuid" in data:
        request.META["HTTP_SLUG"] = data["uuid"]
    view_func, v_args, v_kwargs = resolve(path)
    v_kwargs.update(kwargs)
    response = view_func(request, *v_args, **v_kwargs)  # pylint: disable=E1102
    return response


def delete_tenant(slug):
    # delete roles associated with this tenant
    # Role.objects.filter(tenant_ref__uuid=slug).delete()
    try:
        response = internal_view_request("/api/tenant/%s" % slug,
                                         method_name="delete",
                                         data={'uuid': slug},
                                         kwargs={'force_delete': True})
        if response.status_code != 204:
            logger.error("Delete tenant status code %s", response.status_code)
            response.render()
            logger.error("response: %s", response)
            return False
    except:  # noqa
        logger.exception("Failed in deleting tenant %s", slug)
        return False
    return True


def get_keystone_version(auth_url):
    if auth_url and "v3" in auth_url:
        return 3
    else:
        return 2


def get_keystone_client(auth_url):
    if auth_url and "v3" in auth_url:
        return client_v3
    else:
        return client_v2


def remove_nonlocal_users(dry_run=False):
    users = get_user_model().objects.filter(local=False)
    for duser in users:
        user_act = UserActivity.objects.get(name=duser.name)
        if not dry_run:
            user_act.delete()
            duser.delete()
            print("Deleted non-local user %s" % duser.username)
        else:
            print("Non-local user %s will be deleted" % duser.username)

    return


def remove_nonlocal_users_tenants(dry_run=False):
    remove_nonlocal_users(dry_run=dry_run)
    tenants = Tenant.objects.filter(json_data__local=False)
    for dtenant in tenants:
        # delete all roles with this tenant
        # UserRole.objects.filter(tenant_ref=dtenant).delete()
        try:
            if not dry_run:
                delete_tenant(dtenant.uuid)
                print("Deleted non-local tenant %s" % dtenant.name)
            else:
                print("Non-local tenant %s will be deleted" % dtenant.name)
        except:  # noqa
            logger.exception("Deletion failed for non-local tenant %s",
                             dtenant.name)
            continue
    return


def cleanup_users_tenants(users=None, tenants=None, dry_run=False):
    """
    For each remote user, if not in keystone, remove locally
    For each remote tenant, if not in keystone, remove corresponding
     roles and the local tenant object
    """
    # logger.debug("Cleaning up non-existent non-local users and tenants")
    logger.warning("Starting cleanup")
    openstack_config = get_openstack_config()
    if(not openstack_config or
       not openstack_config.use_keystone_auth):
        logger.warning("Not using keystone auth, "
                       "removing all non-local tenants and user")
        remove_nonlocal_users_tenants(dry_run=dry_run)
        return False

    auth_url = os_utils.getAuthURLFromConfig(openstack_config)
    insecure = openstack_config.insecure
    region_name = None
    if openstack_config.region:
        region_name = openstack_config.region

    del_tenants = []
    (atenants, client) = get_accessible_tenants(openstack_config,
                                                auth_url=auth_url,
                                                insecure=insecure,
                                                region_name=region_name)
    if not client:
        logger.error("Failed to initialize keystone client, "
                     "tenant/user cleanup failed")
        return False

    # keystone V2 requires access to admin URL to delete users and
    # tenants
    if (type(client) == client_v2.Client
            and not openstack_config.use_admin_url):
        logger.info("use_admin_url is set to False, "
                    "not deleting keystone users and tenants")
        return False

    if not atenants:
        logger.warn("Unexpected empty list of tenants from keystone, "
                    "tenant/user cleanup aborted")
        return False

    if tenants is None:
        tenants = Tenant.objects.filter(
            json_data__local=False).select_for_update()

    atenant_map = {}
    for atenant in atenants:
        atenant_map[atenant.id] = atenant

    # check if all local tenants still exist in keystone
    for tenant in tenants:
        uuid = tenant.uuid.split("tenant-")[1].replace('-', '')
        if uuid not in atenant_map:
            print("Tenant %s (%s) not found in OpenStack, marking for deletion" % (tenant.name, uuid))  # noqa
            del_tenants.append(tenant)
        else:
            print("Tenant %s (%s) present in OpenStack, not deleting" % (tenant.name, uuid))  # noqa

    for dtenant in del_tenants:
        # delete all roles with this tenant
        # UserRole.objects.filter(tenant_ref=dtenant).delete()
        # Role.objects.filter(tenant_ref__uuid=dtenant.slug).delete()
        try:
            if not dry_run:
                delete_tenant(dtenant.uuid)
                print("Deleted non-existent tenant %s" % dtenant.name)
        except:  # noqa
            print("Tenant deletion failed")
            continue

    if users is None:
        users = get_user_model().objects.filter(
            local=False).select_for_update()

    del_users = []
    for user in users:
        uuid = user.uuid.split("user-")[1].replace('-', '')
        if not user.access.count():
            del_users.append(user)
            continue

        ruser = None
        try:
            ruser = client.users.get(user=uuid)
        except Exception as e:
            logger.info("Error checking user %s in keystone: %s" % (uuid, e))

        if not ruser:
            del_users.append(user)
            print("User %s (%s) not found in OpenStack, marking for deletion" % (user, uuid))  # noqa
        else:
            print("User %s (%s) present in OpenStack, not deleting" % (user, uuid))  # noqa

    for duser in del_users:
        username = duser.username
        if not dry_run:
            user_act = UserActivity.objects.get(name=duser.name)
            user_act.delete()
            duser.delete()
            print("Deleted non-existent user %s" % username)

    return True


######## MAIN ########################################################################
import argparse

from avi.infrastructure.db_transaction import db_transaction

DRY_RUN = False


@db_transaction
def os_cleanup():
    global DRY_RUN
    cleanup_users_tenants(dry_run=DRY_RUN)


help_str = '''
Avi utility script to delete stale tenants and users imported from OpenStack.
'''
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=help_str, formatter_class=lambda prog: argparse.ArgumentDefaultsHelpFormatter(prog, max_help_position=30))  # noqa
    parser.add_argument('-r', '--dryrun', required=False,
            action='store_true', default=False, help='Print the tenant, and users imported from Keystone that will be cleaned-up')  # noqa
    args = parser.parse_args()
    DRY_RUN = args.dryrun
    os_cleanup()
