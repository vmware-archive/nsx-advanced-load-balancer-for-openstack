# Control Script to Update Contrail BGPaaS instance

#### Problem
Contrail BGPaaS instance was not accepting peer session from Avi SE when
BGP Peering is enabled and configured in Avi. Contrail BGPaaS instance
will accept peer requests only if the request is initiated from a known
VMI (Virtual Machine Interface). BGPaaS instance keeps a list of VMIs it
will accept peering session from.

#### Solution
Add Avi SE VMI to Contrail BGPaaS instance. While this can be done
manually, it is cumbersome to do so, especially when VSes are migrated
and scaled-out dynamically.
This script is to automate the process. User still needs to create an
alert for events CC_IP_ATTACHED and CC_IP_DETACHED and add the Control
Script to alert action. The Control Script needs to updated with BGPaaS
instance id in order to update particular instance; script won't work
without doing so.


#### Note
Works with Avi Version 18.1.4 and above
