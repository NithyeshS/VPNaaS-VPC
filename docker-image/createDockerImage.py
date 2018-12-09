import os

DOCKER_IMAGE_NAME="vpnaas-ubuntuv2"

def execute(cmd):
    print (cmd)
    os.system(cmd)
    
def imageExists(imageName):
    cmd="sudo docker images -q {} > tmp".format(imageName)
    execute(cmd)
    imageID=open("tmp","r").read()
    cmd="sudo rm tmp"
    execute(cmd)
    return (imageID != "")

if not os.path.exists("DockerFile"):
    print("Docker file is missing. Exiting..")
else:
    if imageExists(DOCKER_IMAGE_NAME):
        print("Docker image " + DOCKER_IMAGE_NAME + " already exists")
    else:
        print("Docker file found. Building image...")
        cmd = "sudo docker build -f DockerFile -t {} .".format(DOCKER_IMAGE_NAME)
        execute(cmd)
