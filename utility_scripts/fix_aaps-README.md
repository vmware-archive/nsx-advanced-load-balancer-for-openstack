What
----
This script removes subnet CIDR from allowed address pair entries from
all SE data ports.

Why
----
Having subnet CIDR (e.g. 10.10.20.3/24) in AAP entry makes underlying
SDN think that the VM is going to respond to the packets from IP address
in that range even though there is no real VM hosting that IP. The SDN
florwards flows to SE VM for all IPs in that range when it recieves such
packets and there is no port (or VM) actively using the IP address.

How
----
This script needs to run on controller node. Steps described in JIRA
AV-39027.

Limitations
----
The script is tested only when Avi is configured in provider mode. The
script uses the admin tenant client to update all the entries. If the
SEs are configured in tenant only mode, it doesn't work yet.
