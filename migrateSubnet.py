import os
import json
import time
import re
import sys
import os.path


'''
FUNCTIONS
'''
DOCKER_IMAGE_NAME="ubuntu"

def execute(cmd):
    print(cmd)
    os.system(cmd)

def createVeth(veth1,veth2):
    cmd  = "sudo ansible-playbook ansible_scripts/createVeth.yml -e '{ veth1: "+ veth1 +", veth2: "+ veth2 +" }'"
    execute(cmd)

def attachVeth(vethIface,containerName):
    cmd="sudo docker inspect --format '{{.State.Pid}}' "
    cmd=cmd + containerName
    cmd=cmd + " > tmp"
    execute(cmd)
    containerPID=open("tmp","r").read().rstrip()
    cmd="sudo rm tmp"
    execute(cmd)

    cmd="sudo ip link set netns {} dev {} up".format(containerPID,vethIface)
    execute(cmd)

def attachToBridge(iface,bridgeName):
    cmd = "sudo ansible-playbook ansible_scripts/attachToBridge.yml -e '{ bridgeName: "+ bridgeName +", vethName: "+ iface +" }'"
    execute(cmd)

def assignIPToVeth(vethIface,containerName,ipaddress):
    ipaddcmd="ip addr add {} dev {}".format(ipaddress,vethIface)
    cmd="sudo docker exec -it --privileged {} {}".format(containerName,ipaddcmd)
    execute(cmd)

def runCmdInContainer(containerName,routecmd):
    cmd="sudo docker exec -it --privileged {} {}".format(containerName,routecmd)
    execute(cmd)




"""
SUBNET MIGRATION
"""

inputCidr=sys.argv[1]

inputTenantid=sys.argv[2]

newVpcid=sys.argv[3]

#checking existing count of subnets in new VPC
line = open('/var/ece792/{}/vpc/vpc-{}-db.json'.format(inputTenantid,newVpcid))
responseJson = json.load(line)
chk2 = len(responseJson['Subnet'])
chk2 +=  1

#reading the VPC db file where the subnet originally exists
line = open('/var/ece792/{}/{}-db.json'.format(inputTenantid,inputTenantid))
responseJson = json.load(line)


vpc = []
for i in range(len(responseJson['VPCList'])):
    vpc.append(responseJson['VPCList'][i]['Name'])

for i in range(len(vpc)):
    line = open('/var/ece792/{}/vpc/{}-db.json'.format(inputTenantid,vpc[i]))
    responseJson = json.load(line)
    for j in range(len(responseJson['Subnet'])):
        if inputCidr == responseJson['Subnet'][j]['CIDRBlock']:
            inputVpcid=str(vpc[i]).rstrip().split("-")[-1]
            break


#searching for the subnet inside the old VPC

line = open('/var/ece792/{}/vpc/vpc-{}-db.json'.format(inputTenantid,inputVpcid))
responseJson = json.load(line)
for i in range(len(responseJson['Subnet'])):
    if inputCidr==responseJson['Subnet'][i]['CIDRBlock']:
        bridgeIf=responseJson['Subnet'][i]['Bridge Interface']
        subnetName=responseJson['Subnet'][i]['Name']
        chk = i



#removing the subnet from the old VPC

SubnetIfaceNs="{}{}{}gw-n".format(subnetName,inputVpcid,inputTenantid)
cmd = " ip link del {}".format(SubnetIfaceNs)
RouterContainer = "vpc-{}-{}-gw".format(inputVpcid,inputTenantid)
runCmdInContainer(RouterContainer,cmd)


#delete interface from subnet Bridge

subnetIfaceBr="{}{}{}gw-b".format(subnetName,inputVpcid,inputTenantid)
cmd = "sudo brctl delif {} {}".format(bridgeIf, subnetIfaceBr)
execute(cmd)
cmd="sudo ip link delete {}".format(subnetIfaceBr)
execute(cmd)

'''
Deleting old routes
'''

#Old VPC VPN Gateways

vpnGateway1Name="vpc-{}-{}-vpn-gw1".format(inputVpcid,inputTenantid)
vpnGateway2Name="vpc-{}-{}-vpn-gw2".format(inputVpcid,inputTenantid)

routecmd="ip r del {}".format(inputCidr)
runCmdInContainer(vpnGateway1Name,routecmd)
runCmdInContainer(vpnGateway2Name,routecmd)


#New VPC Gateway

vpcGatewayName="vpc-{}-{}-gw".format(newVpcid,inputTenantid)

routecmd="ip r del {}".format(inputCidr)
runCmdInContainer(vpcGatewayName,routecmd)

#VPC3 VPN Gateways

remotegw1="vpc-{}-{}-vpn-gw1".format(newVpcid,inputTenantid)
remotegw2="vpc-{}-{}-vpn-gw2".format(newVpcid,inputTenantid)
remoteVpnGw1Tun1="{}-{}g1-t1".format(inputTenantid,newVpcid)
remoteVpnGw1Tun2="{}-{}g1-t2".format(inputTenantid,newVpcid)

remoteVpnGw2Tun1="{}-{}g2-t1".format(inputTenantid,newVpcid)
remoteVpnGw2Tun2="{}-{}g2-t2".format(inputTenantid,newVpcid)


routecmd = "ip r del {}".format(inputCidr)
runCmdInContainer(remotegw1, routecmd)
runCmdInContainer(remotegw2, routecmd)

#VPN Servers

transitVPNServer1="{}-vpn1".format(inputTenantid)
transitVPNServer2="{}-vpn2".format(inputTenantid)

transit1Tun1="{}-{}t1-t1".format(inputTenantid,inputVpcid)
transit1Tun2="{}-{}t1-t2".format(inputTenantid,inputVpcid)

transit2Tun1="{}-{}t2-t1".format(inputTenantid,inputVpcid)
transit2Tun2="{}-{}t2-t2".format(inputTenantid,inputVpcid)


routecmd="ip r del {}".format(inputCidr)
runCmdInContainer(transitVPNServer1,routecmd)
runCmdInContainer(transitVPNServer2,routecmd)

'''
Shifting to new VPC
'''
#creating new veth pair

subnetIfaceNs="{}{}{}gw-n".format(subnetName,newVpcid,inputTenantid)
subnetIfaceBr="{}{}{}gw-b".format(subnetName,newVpcid,inputTenantid)
createVeth(subnetIfaceNs, subnetIfaceBr)


#Attaching to subnet Bridge

attachToBridge(subnetIfaceBr, bridgeIf)


#Attaching to new VPC Router Container

vpcGatewayName="vpc-{}-{}-gw".format(newVpcid,inputTenantid)
attachVeth(subnetIfaceNs, vpcGatewayName)

subnetCidr=inputCidr[:-4] +'1/24'
assignIPToVeth(subnetIfaceNs,vpcGatewayName,subnetCidr)
subnetCidrStart = inputCidr[:-4] + '2'
subnetCidrEnd = inputCidr[:-4] + '254'
dnsmasqcmd = "dnsmasq --interface={} --except-interface=lo --bind-interfaces -F {},{} ".format(subnetIfaceNs,subnetCidrStart,subnetCidrEnd)
runCmdInContainer(vpcGatewayName, dnsmasqcmd)




'''
Adding new routes
'''

#VPC1Gateway


vpcGatewayName="vpc-{}-{}-gw".format(inputVpcid,inputTenantid)


if chk2%2 == 0:
    routecmd="ip r add {} via 172.16.1.2".format(inputCidr)
else:
    routecmd="ip r add {} via 172.16.1.3".format(inputCidr)
runCmdInContainer(vpcGatewayName,routecmd)


#VPC1 VPN Gateways

remotegw1="vpc-{}-{}-vpn-gw1".format(inputVpcid,inputTenantid)
remotegw2="vpc-{}-{}-vpn-gw2".format(inputVpcid,inputTenantid)
remoteVpnGw1Tun1="{}-{}g1-t1".format(inputTenantid,inputVpcid)
remoteVpnGw1Tun2="{}-{}g1-t2".format(inputTenantid,inputVpcid)

remoteVpnGw2Tun1="{}-{}g2-t1".format(inputTenantid,inputVpcid)
remoteVpnGw2Tun2="{}-{}g2-t2".format(inputTenantid,inputVpcid)



if chk % 2 == 0:
    routecmd = "ip r add {} dev {}".format(inputCidr, remoteVpnGw1Tun1)
else:
    routecmd = "ip r add {} dev {}".format(inputCidr, remoteVpnGw1Tun2)
runCmdInContainer(remotegw1, routecmd)


if chk % 2 == 0:
    routecmd = "ip r add {} dev {}".format(inputCidr, remoteVpnGw2Tun1)
else:
    routecmd = "ip r add {} dev {}".format(inputCidr, remoteVpnGw2Tun2)
runCmdInContainer(remotegw2, routecmd)

#VPC3 VPN Gateways

vpnGateway1Name="vpc-{}-{}-vpn-gw1".format(newVpcid,inputTenantid)
vpnGateway2Name="vpc-{}-{}-vpn-gw2".format(newVpcid,inputTenantid)

routecmd="ip r add {} via 172.16.1.1".format(inputCidr)
runCmdInContainer(vpnGateway1Name,routecmd)
runCmdInContainer(vpnGateway2Name,routecmd)

#VPN Servers

transitVPNServer1="{}-vpn1".format(inputTenantid)
transitVPNServer2="{}-vpn2".format(inputTenantid)

transit1Tun1="{}-{}t1-t1".format(inputTenantid,newVpcid)
transit1Tun2="{}-{}t1-t2".format(inputTenantid,newVpcid)

transit2Tun1="{}-{}t2-t1".format(inputTenantid,newVpcid)
transit2Tun2="{}-{}t2-t2".format(inputTenantid,newVpcid)



if chk%2 == 0:
    routecmd="ip r add {} dev {}".format(inputCidr,transit1Tun1)
else:
    routecmd="ip r add {} dev {}".format(inputCidr,transit1Tun2)
runCmdInContainer(transitVPNServer1,routecmd)


if chk%2 == 0:
    routecmd="ip r add {} dev {}".format(inputCidr,transit2Tun1)
else:
    routecmd="ip r add {} dev {}".format(inputCidr,transit2Tun2)
runCmdInContainer(transitVPNServer2,routecmd)
