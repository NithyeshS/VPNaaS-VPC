import os
import json
import time
import re
import xml.etree.cElementTree as ET
import sys
import os.path

DOCKER_IMAGE_NAME="vpnaas-ubuntuv2"

KEEPALIVED_CONF_DATA= """! Configuration File for keepalived

global_defs {
   notification_email {
     sysadmin@mydomain.com
     support@mydomain.com
   }
   notification_email_from lb2@mydomain.com
   smtp_server localhost
   smtp_connect_timeout 30
}

vrrp_instance VI_1 {
    state MASTER
    interface iface1
    virtual_router_id vpcID
    priority priorityValue
    advert_int 1
    authentication {
        auth_type PASS
        auth_pass 1111
    }
    virtual_ipaddress {
        172.16.1.4
    }
}"""

def execute(cmd):
    print(cmd)
    os.system(cmd)

# Create a container
def createContainer(name,image):
	cmd = "sudo ansible-playbook ansible_scripts/createContainer.yml -e '{ containerName: "+ name +", imageName: "+ image +" }'"
	execute(cmd)
    
#Create a L2 bridge
def createL2Bridge(bridgeName):
    cmd = "sudo ansible-playbook ansible_scripts/createL2Bridge.yml -e '{ bridgeName: "+ bridgeName +" }'"
    execute(cmd)

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

def setupGRETunnel(tunnelDevName,local,remote,containerName):
    tunnelcmd="ip tunnel add {} mode gre local {} remote {}".format(tunnelDevName,local,remote)
    cmd="sudo docker exec -it --privileged {} {}".format(containerName, tunnelcmd)
    execute(cmd)
    tunnelcmd="ip link set {} up".format(tunnelDevName)
    cmd="sudo docker exec -it --privileged {} {}".format(containerName, tunnelcmd)
    execute(cmd)

def containerExists(containerName):
    cmd="sudo docker ps -f name={} -q > tmp".format(containerName)
    execute(cmd)
    containerID=open("tmp","r").read().rstrip()
    cmd="sudo rm tmp"
    execute(cmd)
    return (containerID != "")

def configureHotStandby(container1,container1Iface,container2,container2Iface,):

    confData1=KEEPALIVED_CONF_DATA
    pattern="iface1"
    substitution="{}".format(container1Iface)
    pat="priorityValue"
    sub="100"
    pat1="vpcID"
    sub1="{}".format(vpcid)
    confData1=re.sub(pattern,substitution,confData1,1)
    confData1=re.sub(pat,sub,confData1,1)
    confData1=re.sub(pat1,sub1,confData1,1)

    confData2=KEEPALIVED_CONF_DATA
    pattern="iface1"
    substitution="{}".format(container2Iface)
    pat="priorityValue"
    sub="101"
    pat1="vpcID"
    sub1="{}".format(vpcid)
    confData2=re.sub(pattern,substitution,confData2,1)
    confData2=re.sub(pat,sub,confData2,1)
    confData2=re.sub(pat1,sub1,confData2,1)

    cmd="rm /etc/keepalived/keepalived.conf"
    runCmdInContainer(container1,cmd)
    runCmdInContainer(container2,cmd)

    cmd="touch /etc/keepalived/keepalived.conf"
    runCmdInContainer(container1,cmd)
    runCmdInContainer(container2,cmd)

    for line in confData1.split("\n"):
        cmd='bash -c "echo {} >> /etc/keepalived/keepalived.conf"'.format(line)
        runCmdInContainer(container1,cmd)

    for line in confData2.split("\n"):
        cmd='bash -c "echo {} >> /etc/keepalived/keepalived.conf"'.format(line)
        runCmdInContainer(container2,cmd)

    cmd='bash -c "echo net.ipv4.ip_nonlocal_bind = 1 >> /etc/sysctl.conf"'
    runCmdInContainer(container1,cmd)
    runCmdInContainer(container2,cmd)

    cmd="sysctl -p"
    runCmdInContainer(container1,cmd)
    runCmdInContainer(container2,cmd)

    cmd="service keepalived start"
    runCmdInContainer(container1,cmd)
    runCmdInContainer(container2,cmd)

#Database content to be stored in /var directory
vpcData={} # Stored in /var/ece792/<<tenant-id>>/vpc/<<vpc-id>>-db.json
tenantData={} # Stored in /var/ece792/<<tenant-id>>/<<tenant-id>>-db.json

#open and read template json file
line = open(sys.argv[1])
responseJson = json.load(line)

#storing different subnets and their corresponding CIDR blocks
subnetname = []
cidr = []
vpcid=responseJson['Resources']['VPC']['Properties']['VpcID']
tenantid=responseJson['Resources']['VPC']['Properties']['TenantID']
vpcname=responseJson['Resources']['VPC']['Properties']['Name']

vpcJSONFilePath='/var/ece792/{}/vpc/vpc-{}-db.json'.format(tenantid,vpcid)
tenantJSONFilePath='/var/ece792/{}/{}-db.json'.format(tenantid,tenantid)

vpcData['VPC']={
    'TenantID' : tenantid,
    'VpcID' : vpcid,
    'Name' : vpcname
    }

for i in range(len(responseJson['Resources']['Subnets'])):
    subnetname.append(responseJson['Resources']['Subnets'][i]['Name'])
    cidr.append(responseJson['Resources']['Subnets'][i]['CIDRBlock'])

'''
Configuring L2 network for each subnet
1. Adding Linux Bridge
2. Setting it up
'''
vpcData['Subnet'] = []
for i in range(len(subnetname)):
    # Bridge name is of format <<subnetname>>-<<vpcid>>-<<tenantid>>-br
    subnetBrName="{}{}{}-br".format(subnetname[i],vpcid,tenantid)

    #1,2
    createL2Bridge(subnetBrName)

    # Write subnets info to DB file
    vpcData['Subnet'].append({
        'Name' : '{}'.format(subnetname[i]),
        'CIDRBlock' : '{}'.format(cidr[i]),
        'Bridge Interface' : '{}'.format(subnetBrName)
    })


#Creating container for RouterVM
vpcGatewayName="vpc-{}-{}-gw".format(vpcid,tenantid)
createContainer(vpcGatewayName,DOCKER_IMAGE_NAME)

vpcData['VPCGateway']={
    'Name' : '{}'.format(vpcGatewayName)
    }

vpcData['VPCGateway']['Interfaces']=[]
    
'''
Configuring Router NameSpace
1. Adding veth pair for every subnet
2. Setting interface2 up and connecting it to the bridge used in the corresponding subnet L2 network
3. Sending interface1 to the RouterNS and setting it up
4. Assigning first available ip address of the subnet to interface1
5. Setting up light DHCP server(dnsmasq) at interface1
'''
for i in range(len(subnetname)):
    subnetIfaceNs="{}{}{}gw-n".format(subnetname[i],vpcid,tenantid)
    subnetIfaceBr="{}{}{}gw-b".format(subnetname[i],vpcid,tenantid)
    subnetBrName="{}{}{}-br".format(subnetname[i],vpcid,tenantid)
    #1
    createVeth(subnetIfaceBr,subnetIfaceNs)

    attachToBridge(subnetIfaceBr,subnetBrName)

    #3
    attachVeth(subnetIfaceNs,vpcGatewayName)

    #4
    subnetCidr=cidr[i][:-4] +'1/24'
    assignIPToVeth(subnetIfaceNs,vpcGatewayName,subnetCidr)

    #5
    subnetCidrStart=cidr[i][:-4] + '2'
    subnetCidrEnd=cidr[i][:-4] + '254'
    dnsmasqcmd="dnsmasq --interface={} --except-interface=lo --bind-interfaces -F {},{} ".format(subnetIfaceNs,subnetCidrStart,subnetCidrEnd)
    runCmdInContainer(vpcGatewayName,dnsmasqcmd)

    vpcData['VPCGateway']['Interfaces'].append({
        'Interface' : '{}'.format(subnetIfaceNs),
        'Subnet' : '{}'.format(subnetname[i]),
        'IPAddress' : '{}'.format(subnetCidr)
        })

#Setting up the instances

#Storing the count of different instances and the corresponding subnets they are connected to
instcount = []
instsubn = []
for i in range(len(responseJson['Resources']['Instances'])):
    instcount.append(responseJson['Resources']['Instances'][i]['Count'])
    instsubn.append(responseJson['Resources']['Instances'][i]['NetworkID'])

'''
Creating namespace for each instance
1. Adding namespace
2. Adding veth pair
3. Setting interface2 up and connecting it to the bridge used in the corresponding subnet L2 network
4. Sending interface1 to the instance namespace and setting it up
5. Running dhclient to get ip address from the DHCP server
'''
count=0
for i in range(len(instcount)):
    for j in range(int(instcount[i])):

        instanceNSName="{}-{}-{}-S-{}".format(vpcid,tenantid,instsubn[i],j)
        instanceVethBr="{}{}{}S{}-b".format(vpcid,tenantid,instsubn[i],j)
        instanceVethNs="{}{}{}S{}-i".format(vpcid,tenantid,instsubn[i],j)
        subnetBrName="{}{}{}-br".format(subnetname[i],vpcid,tenantid)
        #1
        createContainer(instanceNSName,DOCKER_IMAGE_NAME)

        #2
        createVeth(instanceVethBr,instanceVethNs)

        attachToBridge(instanceVethBr,subnetBrName)

        #4
        attachVeth(instanceVethNs,instanceNSName)

        #5
        removeroutecmd="ip r del default"
        runCmdInContainer(instanceNSName,removeroutecmd)
        dhclientcmd="dhclient -i {}".format(instanceVethNs)
        runCmdInContainer(instanceNSName,dhclientcmd)

        count += 1



vpcData['Instances_Information']={
    'Count' : count
}
vpcData['Instances_Information']['Instance'] = []

for i in range(len(instcount)):
    for j in range(int(instcount[i])):
        vpcData['Instances_Information']['Instance'].append({
            'Name' : "{}-{}-{}-S-{}".format(vpcid,tenantid,instsubn[i],j),
            'Interfaces' : "{}{}{}S{}-i".format(vpcid,tenantid,instsubn[i],j),
            'Subnet' : "{}{}{}-br".format(subnetname[i],vpcid,tenantid)
        })


print('\n')
print("----------------------------")
print("       VPN DEPLOYMENT       ")
print("----------------------------")
print('\n')
'''
Creating VPN setup for VPC
1. Create VPN GW to VPC GW bridge
2. Attach veth pair from bridge to VPC GW
3. Creating VPN GW1
4. Attach veth pair from bridge to VPN GW1
5. Creating VPN GW2
6. Attach veth pair from bridge to VPN GW2
7. Set routes to VPC CIDR blocks in VPN GW1 and VPN GW2
'''

vpcVpnBridge="{}{}-vpnbr".format(vpcid,tenantid)
vpcGWIfaceBr="{}{}gw-b".format(vpcid,tenantid)
vpcGWIfaceNs="{}{}gw-n".format(vpcid,tenantid)

#1
createL2Bridge(vpcVpnBridge)

#2
createVeth(vpcGWIfaceBr,vpcGWIfaceNs)

attachToBridge(vpcGWIfaceBr,vpcVpnBridge)

attachVeth(vpcGWIfaceNs,vpcGatewayName)

assignIPToVeth(vpcGWIfaceNs, vpcGatewayName, "172.16.1.1/24")

vpnGW1IfaceBr="{}{}vgw1-b".format(vpcid,tenantid)
vpnGW1IfaceNs="{}{}vgw1-n".format(vpcid,tenantid)
#3
vpnGateway1Name="vpc-{}-{}-vpn-gw1".format(vpcid,tenantid)
createContainer(vpnGateway1Name,DOCKER_IMAGE_NAME)

#4
createVeth(vpnGW1IfaceBr,vpnGW1IfaceNs)

attachToBridge(vpnGW1IfaceBr,vpcVpnBridge)

attachVeth(vpnGW1IfaceNs,vpnGateway1Name)

assignIPToVeth(vpnGW1IfaceNs,vpnGateway1Name,"172.16.1.2/24")

#5
vpnGateway2Name="vpc-{}-{}-vpn-gw2".format(vpcid,tenantid)
createContainer(vpnGateway2Name,DOCKER_IMAGE_NAME)

vpnGW2IfaceBr="{}{}vgw2-b".format(vpcid,tenantid)
vpnGW2IfaceNs="{}{}vgw2-n".format(vpcid,tenantid)
#6
createVeth(vpnGW2IfaceBr,vpnGW2IfaceNs)

attachToBridge(vpnGW2IfaceBr,vpcVpnBridge)

attachVeth(vpnGW2IfaceNs,vpnGateway2Name)

assignIPToVeth(vpnGW2IfaceNs,vpnGateway2Name,"172.16.1.3/24")
#7
for network in cidr:
    routecmd="ip r add {} via 172.16.1.1".format(network)
    runCmdInContainer(vpnGateway1Name,routecmd)
    runCmdInContainer(vpnGateway2Name,routecmd)

'''
Creating transit VPC with 2 VPN servers for this tenant if not already present
1. Get ip netns list and check for transit VPC and create if not present
2. Attach 4 veth pairs from VPN GW1 (2) and VPN GW2 (2) to both servers
3. Add IP addresses as per need
'''
#1
spineVPNServer1="{}-spine1".format(tenantid)
spineVPNServer2="{}-spine2".format(tenantid)
transitVPNServer1="{}-vpn1".format(tenantid)
transitVPNServer2="{}-vpn2".format(tenantid)
isFirstVPC=False

if not containerExists(spineVPNServer1):
    isFirstVPC=True
    createContainer(spineVPNServer1,DOCKER_IMAGE_NAME)

if not containerExists(spineVPNServer2):
    isFirstVPC=True
    createContainer(spineVPNServer2,DOCKER_IMAGE_NAME)

# Create tenant dir in /var folder
if isFirstVPC:
    if not os.path.exists("/var/ece792/{}/vpc/".format(tenantid)):
        os.makedirs("/var/ece792/{}/vpc/".format(tenantid))

# TODO - Get VPC count to check if we need to create a new transit VPC
if not isFirstVPC:
    resJSON=open(tenantJSONFilePath)
    tenantJSONFileData=json.load(resJSON)
    vpcList=tenantJSONFileData['VPCList']
    vpcCount=len(vpcList)
else:
    #This is the first VPC created for this tenant
    vpcCount=0

# Check how many transit servers exist already.
numOfTransitServers = (vpcCount/4) * 2

transitVPNServer1="{}-vpn{}".format(tenantid,numOfTransitServers+1)
transitVPNServer2="{}-vpn{}".format(tenantid,numOfTransitServers+2)

leafSpine1Spine="{}ls{}-s".format(tenantid,numOfTransitServers+1)
leafSpine1Leaf="{}ls{}-l".format(tenantid,numOfTransitServers+1)

leafSpine2Spine="{}ls{}-s".format(tenantid,numOfTransitServers+2)
leafSpine2Leaf="{}ls{}-l".format(tenantid,numOfTransitServers+2)

leafSpine1SpineIP="10.1.{}.1/24".format(numOfTransitServers+1)
leafSpine1LeafIP="10.1.{}.2/24".format(numOfTransitServers+1)

leafSpine2SpineIP="10.1.{}.1/24".format(numOfTransitServers+2)
leafSpine2LeafIP="10.1.{}.2/24".format(numOfTransitServers+2)

if (vpcCount % 4 == 0):
    if not containerExists(transitVPNServer1):
        createContainer(transitVPNServer1,DOCKER_IMAGE_NAME)
        createVeth(leafSpine1Spine,leafSpine1Leaf)
        attachVeth(leafSpine1Spine,spineVPNServer1)
        assignIPToVeth(leafSpine1Spine,spineVPNServer1,leafSpine1SpineIP)
        attachVeth(leafSpine1Leaf,transitVPNServer1)
        assignIPToVeth(leafSpine1Leaf,transitVPNServer1,leafSpine1LeafIP)
    if not containerExists(transitVPNServer2):
        createContainer(transitVPNServer2,DOCKER_IMAGE_NAME)
        createVeth(leafSpine2Spine,leafSpine2Leaf)
        attachVeth(leafSpine2Spine,spineVPNServer2)
        assignIPToVeth(leafSpine2Spine,spineVPNServer2,leafSpine2SpineIP)
        attachVeth(leafSpine2Leaf,transitVPNServer2)
        assignIPToVeth(leafSpine2Leaf,transitVPNServer2,leafSpine2LeafIP)

# Set default route in leaf servers to point to spine servers
routecmd="ip route change default via {}".format(leafSpine1SpineIP[:-3])
runCmdInContainer(transitVPNServer1,routecmd) 

routecmd="ip route change default via {}".format(leafSpine2SpineIP[:-3])
runCmdInContainer(transitVPNServer2,routecmd)

#2.1 Create the veth peers
vpnGW1T1IfaceGW="{}{}g1t1-g".format(vpcid,tenantid)
vpnGW1T1IfaceTenant="{}{}g1t1-t".format(vpcid,tenantid)

vpnGW1T2IfaceGW="{}{}g1t2-g".format(vpcid,tenantid)
vpnGW1T2IfaceTenant="{}{}g1t2-t".format(vpcid,tenantid)

vpnGW2T1IfaceGW="{}{}g2t1-g".format(vpcid,tenantid)
vpnGW2T1IfaceTenant="{}{}g2t1-t".format(vpcid,tenantid)

vpnGW2T2IfaceGW="{}{}g2t2-g".format(vpcid,tenantid)
vpnGW2T2IfaceTenant="{}{}g2t2-t".format(vpcid,tenantid)

createVeth(vpnGW1T1IfaceTenant,vpnGW1T1IfaceGW)

createVeth(vpnGW1T2IfaceTenant,vpnGW1T2IfaceGW)

createVeth(vpnGW2T1IfaceTenant,vpnGW2T1IfaceGW)

createVeth(vpnGW2T2IfaceTenant,vpnGW2T2IfaceGW)

#2.2 Move interfaces to namespaces
attachVeth(vpnGW1T1IfaceGW,vpnGateway1Name)

attachVeth(vpnGW1T2IfaceGW,vpnGateway1Name)

attachVeth(vpnGW2T1IfaceGW,vpnGateway2Name)

attachVeth(vpnGW2T2IfaceGW,vpnGateway2Name)

attachVeth(vpnGW1T1IfaceTenant,transitVPNServer1)

attachVeth(vpnGW2T1IfaceTenant,transitVPNServer1)

attachVeth(vpnGW1T2IfaceTenant,transitVPNServer2)

attachVeth(vpnGW2T2IfaceTenant,transitVPNServer2)

vpcList=[]
#2.4 Assign IP
# If this is the first VPC connecting to the transit VPC, use IP from 172.16.2.0/26 subnets.
if isFirstVPC:
    vpn_gw_cidr="172.16.2."
    vpnIPGW1T1Gw="172.16.2.1/26"
    vpnIPGW1T1Transit="172.16.2.2/26"
    vpnIPGW1T2Gw="172.16.2.65/26"
    vpnIPGW1T2Transit="172.16.2.66/26"

    vpnIPGW2T1Gw="172.16.2.129/26"
    vpnIPGW2T1Transit="172.16.2.130/26"
    vpnIPGW2T2Gw="172.16.2.193/26"
    vpnIPGW2T2Transit="172.16.2.194/26"

else:
    resJSON=open(tenantJSONFilePath)
    tenantJSONFileData=json.load(resJSON)
    vpcList=tenantJSONFileData['VPCList']
    vpcCount=len(vpcList)
    vpn_gw_cidr="172.16.{}.".format(vpcCount+2)
    vpnIPGW1T1Gw="172.16.{}.1/26".format(vpcCount+2)
    vpnIPGW1T1Transit="172.16.{}.2/26".format(vpcCount+2)
    vpnIPGW1T2Gw="172.16.{}.65/26".format(vpcCount+2)
    vpnIPGW1T2Transit="172.16.{}.66/26".format(vpcCount+2)

    vpnIPGW2T1Gw="172.16.{}.129/26".format(vpcCount+2)
    vpnIPGW2T1Transit="172.16.{}.130/26".format(vpcCount+2)
    vpnIPGW2T2Gw="172.16.{}.193/26".format(vpcCount+2)
    vpnIPGW2T2Transit="172.16.{}.194/26".format(vpcCount+2)

assignIPToVeth(vpnGW1T1IfaceGW,vpnGateway1Name,vpnIPGW1T1Gw)

assignIPToVeth(vpnGW1T2IfaceGW,vpnGateway1Name,vpnIPGW1T2Gw)

assignIPToVeth(vpnGW2T1IfaceGW,vpnGateway2Name,vpnIPGW2T1Gw)

assignIPToVeth(vpnGW2T2IfaceGW,vpnGateway2Name,vpnIPGW2T2Gw)

assignIPToVeth(vpnGW1T1IfaceTenant,transitVPNServer1,vpnIPGW1T1Transit)

assignIPToVeth(vpnGW2T1IfaceTenant,transitVPNServer1,vpnIPGW2T1Transit)

assignIPToVeth(vpnGW1T2IfaceTenant,transitVPNServer2,vpnIPGW1T2Transit)

assignIPToVeth(vpnGW2T2IfaceTenant,transitVPNServer2,vpnIPGW2T2Transit)

vpcData['VPNGateway'] = {
    'Gateway1Name' : '{}'.format(vpnGateway1Name),
    'Gateway1Interfaces' : ["{}".format(vpnGW1T1IfaceGW), "{}".format(vpnGW1T2IfaceGW)],
    'Gateway2Name' : '{}'.format(vpnGateway2Name),
    'Gateway2Interfaces' : ["{}".format(vpnGW2T1IfaceGW), "{}".format(vpnGW2T2IfaceGW)],
    'CIDR' : '{}'.format(vpn_gw_cidr)
}

transitServersList=[]
if not isFirstVPC:
    resJSON=open(tenantJSONFilePath)
    tenantJSONFileData=json.load(resJSON)
    transitServersList=tenantJSONFileData['TransitVPCServers']
else:
    transitServersList.append({"Name" : "{}".format(spineVPNServer1)})
    transitServersList.append({"Name" : "{}".format(spineVPNServer2)})

transitServersList.append({"Name" : "{}".format(transitVPNServer1)})
transitServersList.append({"Name" : "{}".format(transitVPNServer2)})
tenantData['TransitVPCServers']=transitServersList

vpcList.append({"Name" : "vpc-{}".format(vpcid)})
tenantData['VPCList']=vpcList


'''
Create VPN tunnels from this VPC to other existing VPCs
1. Create tunnel from VGW to transit servers
2. Add current VPC CIDRs to both transit servers
3. For second VPC onwards, get list of VPCs
  3.1 For each VPC, add VPC CIDRs to VPC GW and VGW
'''

#1
vpnGw1Tun1="{}-{}g1-t1".format(tenantid,vpcid)
vpnGw1Tun2="{}-{}g1-t2".format(tenantid,vpcid)

vpnGw2Tun1="{}-{}g2-t1".format(tenantid,vpcid)
vpnGw2Tun2="{}-{}g2-t2".format(tenantid,vpcid)

transit1Tun1="{}-{}t1-t1".format(tenantid,vpcid)
transit1Tun2="{}-{}t1-t2".format(tenantid,vpcid)

transit2Tun1="{}-{}t2-t1".format(tenantid,vpcid)
transit2Tun2="{}-{}t2-t2".format(tenantid,vpcid)

gw1T1localIP=vpn_gw_cidr+"1"
gw1T1remoteIP=vpn_gw_cidr+"2"

gw1T2localIP=vpn_gw_cidr+"65"
gw1T2remoteIP=vpn_gw_cidr+"66"

gw2T1localIP=vpn_gw_cidr+"129"
gw2T1remoteIP=vpn_gw_cidr+"130"

gw2T2localIP=vpn_gw_cidr+"193"
gw2T2remoteIP=vpn_gw_cidr+"194"

setupGRETunnel(vpnGw1Tun1,gw1T1localIP,gw1T1remoteIP,vpnGateway1Name)

setupGRETunnel(vpnGw1Tun2,gw1T2localIP,gw1T2remoteIP,vpnGateway1Name)

setupGRETunnel(vpnGw2Tun1,gw2T1localIP,gw2T1remoteIP,vpnGateway2Name)

setupGRETunnel(vpnGw2Tun2,gw2T2localIP,gw2T2remoteIP,vpnGateway2Name)

setupGRETunnel(transit1Tun1,gw1T1remoteIP,gw1T1localIP,transitVPNServer1)

setupGRETunnel(transit2Tun1,gw1T2remoteIP,gw1T2localIP,transitVPNServer2)

setupGRETunnel(transit1Tun2,gw2T1remoteIP,gw2T1localIP,transitVPNServer1)

setupGRETunnel(transit2Tun2,gw2T2remoteIP,gw2T2localIP,transitVPNServer2)

#2
for i in range(0, len(cidr)):
    if i%2 == 0:
        routecmd="ip r add {} dev {}".format(cidr[i],transit1Tun1)
    else:
        routecmd="ip r add {} dev {}".format(cidr[i],transit1Tun2)
    runCmdInContainer(transitVPNServer1,routecmd)

for i in range(0, len(cidr)):
    if i%2 == 0:
        routecmd="ip r add {} dev {}".format(cidr[i],transit2Tun1)
    else:
        routecmd="ip r add {} dev {}".format(cidr[i],transit2Tun2)
    runCmdInContainer(transitVPNServer2,routecmd)

#2.1 Add this VPC subnets to spine servers
for i in range(0, len(cidr)):
    routecmd1="ip r add {} via {}".format(cidr[i],leafSpine1LeafIP[:-3])
    routecmd2="ip r add {} via {}".format(cidr[i],leafSpine2LeafIP[:-3])
    runCmdInContainer(spineVPNServer1,routecmd1)
    runCmdInContainer(spineVPNServer2,routecmd2)

configureHotStandby(vpnGateway1Name,vpnGW1IfaceNs,vpnGateway2Name,vpnGW2IfaceNs)

# Add local routes to remote VPC's gateway
routecmd="ip r change default via 172.16.1.4"
runCmdInContainer(vpcGatewayName,routecmd)

#3
if not isFirstVPC:
    vpcList=tenantData['VPCList']
    for vpc in vpcList:
        vpcDBFilePath="/var/ece792/{}/vpc/{}-db.json".format(tenantid,vpc['Name'])

        if not os.path.exists(vpcDBFilePath):
            continue

        vpcDBFile=open(vpcDBFilePath)
        vpcDBFileData=json.load(vpcDBFile)

        remoteVPCID=vpcDBFileData['VPC']['VpcID']

        remotegw1=vpcDBFileData['VPNGateway']['Gateway1Name']
        remotegw2=vpcDBFileData['VPNGateway']['Gateway2Name']

        remoteVPCGateway=vpcDBFileData['VPCGateway']['Name']

        remoteVpnGw1Tun1="{}-{}g1-t1".format(tenantid,remoteVPCID)
        remoteVpnGw1Tun2="{}-{}g1-t2".format(tenantid,remoteVPCID)

        remoteVpnGw2Tun1="{}-{}g2-t1".format(tenantid,remoteVPCID)
        remoteVpnGw2Tun2="{}-{}g2-t2".format(tenantid,remoteVPCID)

        remoteSubnets=[]
        tempList=vpcDBFileData['Subnet']

        for item in tempList:
            remoteSubnets.append(item['CIDRBlock'])


        #3.1
        # Add remote routes to this VPC's VPN gateways
        for i in range(0, len(remoteSubnets)):
            if i%2 == 0:
                routecmd="ip r add {} dev {}".format(remoteSubnets[i],vpnGw1Tun1)
            else:
                routecmd="ip r add {} dev {}".format(remoteSubnets[i],vpnGw1Tun2)
            runCmdInContainer(vpnGateway1Name,routecmd)

        for i in range(0, len(remoteSubnets)):
            if i%2 == 0:
                routecmd="ip r add {} dev {}".format(remoteSubnets[i],vpnGw2Tun1)
            else:
                routecmd="ip r add {} dev {}".format(remoteSubnets[i],vpnGw2Tun2)
            runCmdInContainer(vpnGateway2Name,routecmd)


        # Add local routes to remote VPC's VPN gateway
        for i in range(0, len(cidr)):
            if i%2 == 0:
                routecmd="ip r add {} dev {}".format(cidr[i],remoteVpnGw1Tun1)
            else:
                routecmd="ip r add {} dev {}".format(cidr[i],remoteVpnGw1Tun2)
            runCmdInContainer(remotegw1,routecmd)

        for i in range(0, len(cidr)):
            if i%2 == 0:
                routecmd="ip r add {} dev {}".format(cidr[i],remoteVpnGw2Tun1)
            else:
                routecmd="ip r add {} dev {}".format(cidr[i],remoteVpnGw2Tun2)
            runCmdInContainer(remotegw2,routecmd)

with open(vpcJSONFilePath, 'w') as outfile:
    json.dump(vpcData, outfile)

with open(tenantJSONFilePath, 'w') as outfile:
    json.dump(tenantData, outfile)

