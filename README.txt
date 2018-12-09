-------------------------------------------------------------------------------
                                VPN as a Service
-------------------------------------------------------------------------------

Files:
milestone3/
├── ansible.cfg                         - Ansible Conf file
├── ansible_scripts                     - Contains ansible scripts
│   ├── attachToBridge.retry
│   ├── attachToBridge.yml
│   ├── createContainer.retry
│   ├── createContainer.yml
│   ├── createL2Bridge.retry
│   ├── createL2Bridge.yml
│   ├── createVeth.retry
│   └── createVeth.yml
├── docker-image
│   ├── createDockerImage.py            - Script to create initial docker image
│   └── DockerFile                      - Dockerfile used to create image
├── hosts                               - Ansible hosts file
├── infraDeploy.py                      - Script to provision VPC and VPN
├── migrateSubnet.py                    - Script to migrate a subnet
├── vpc-87.json                         - Template JSON files
├── vpc-88.json
├── vpc-89.json
├── vpc-97.json
├── vpc-98.json
└── vpc-99.json


EXECUTE ON HYPERVISOR:

    1. Create the docker image.

        sudo python createDockerImage.py 

    2. Edit VPC template files as needed.

    3. Provision the VPC and VPN. Run for every VPC.

        sudo python infraDeploy.py vpc-97.json

    4. To migrate a subnet

        sudo migrateSubnet.py <<CIDR>> <<tenant-id>> <<vpc-id>>

        Eg. sudo migrateSubnet.py 192.168.78.0/24 t-50 99