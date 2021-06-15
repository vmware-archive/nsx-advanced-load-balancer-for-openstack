############################################################################
# ------------------------------------------------------------------------
# Copyright 2020 VMware, Inc.  All rights reserved. VMware Confidential
# ------------------------------------------------------------------------
###
import argparse
import copy
import django
import os
import sys

sys.path.append("/opt/avi/python/bin/portal")
os.environ["DJANGO_SETTINGS_MODULE"] = "portal.settings_full"
django.setup()

from avi.infrastructure.datastore import Datastore
from avi.image.image_db_utils import save_image_to_db
from avi.protobuf.image_pb2 import Image as ImagePb
from avi.infrastructure.transaction import restore_object


image_template = ImagePb()


def get_specified_image():
    ds = Datastore()
    try:
        the_img = ds.get('image', args.image_uuid)
        return the_img
    except Exception as e:
        print("Error fetching image: %s" % str(e))


def update_img():
    """
    check the se_pkg path and update accordingly
    :return:
    """
    ds = Datastore()
    image_info = ds.get('image', args.image_uuid)
    image_config = image_info.get('config')
    image_config = copy.deepcopy(image_config)
    updated = False
    for civ in image_config.cloud_info_values:
        if civ.cloud_name == "openstack":
            for cdv in civ.cloud_data_values:
                if (cdv.key == "glance_api_versions"
                        and args.glance_api_versions
                        and cdv.values[0] != args.glance_api_versions):
                    print("Old values for glance service: %s" % cdv.values[0])
                    print("New values for glance service: %s" % args.glance_api_versions)  # noqa
                    cdv.values[0] = args.glance_api_versions
                    updated = True

    if updated:
        save_image_to_db(image_config)
        svc = restore_object(image_template, image_config.uuid)
        svc.obj = image_config
        svc.save()
        print("Updated values for Image: %s" % image_config.name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image-uuid", required=True,
                        help="UUID of the image to be updated")
    parser.add_argument("--glance-api-versions", required=False,
                        help="updates supported Glance Service API versions")
    args = parser.parse_args()
    the_img = get_specified_image()
    if not the_img:
        print("Image with uuid %s not found\n" % args.image_uuid)
        sys.exit(1)

    update_img()

"""
Description
-----------
Use this script to alter supported glance API versions in a image
uploaded in Avi Controller.
e.g. update supported glance_api_versions from 2.5-2.9 to 2.5-2.10.

This script will silently ignore the updates if the given values match.

Usage
------
1. Copy this file to Avi Controller.
2. Find out the image uuid of the image you want to update.
3. Run this script like below:
python3 /tmp/update-img-os-supported-versions.py \
    --image-uuid='image-1573d05f-5818-43b3-b749-f4c9a248e64e' \
    --glance-api-versions='2.5-2.10'

Run with --help to see help messages.
"""
