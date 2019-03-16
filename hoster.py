#!/usr/bin/python3
import docker
import argparse
import shutil
import signal
import time
import sys
import os

label_name = "hoster.domains"
enclosing_pattern = "#-----------Docker-Hoster-Domains----------\n"
hosts_path = "/tmp/hosts"
hosts = {}

def signal_handler(signal, frame):
    global hosts
    hosts = {}
    update_hosts_file()
    sys.exit(0)

def main():
    # register the exit signals
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    args = parse_args()
    global hosts_path
    hosts_path = args.file

    dockerClient = docker.APIClient(base_url='unix://%s' % args.socket)
    events = dockerClient.events(decode=True)
    #get running containers
    for c in dockerClient.containers(quiet=True, all=False):
        container_id = c["Id"]
        container = get_container_data(dockerClient, container_id)
        hosts[container_id] = container

    update_hosts_file()

    #listen for events to keep the hosts file updated
    for e in events:
        if e["Type"]!="container": 
            continue
        
        status = e["status"]
        if status =="start":
            container_id = e["id"]
            container = get_container_data(dockerClient, container_id)
            hosts[container_id] = container
            update_hosts_file()

        if status=="stop" or status=="die" or status=="destroy":
            container_id = e["id"]
            if container_id in hosts:
                hosts.pop(container_id)
                update_hosts_file()


def get_container_data(dockerClient, container_id):
    #extract all the info with the docker api
    info = dockerClient.inspect_container(container_id)
    config = info["Config"]
    container_hostname = config["Hostname"]
    container_name = info["Name"].strip("/")
    container_ip = info["NetworkSettings"]["IPAddress"]
    
    is_traefik = config["Image"].startswith("traefik:")

    networks = []
    for values in info["NetworkSettings"]["Networks"].values():
        if not values["Aliases"]: 
            continue
        networks.append({
                "ip": values["IPAddress"] , 
                "name": container_name,
                "domains": set(values["Aliases"] + [container_name, container_hostname])
            })

    if container_ip:
        networks.append({"ip": container_ip, "name": container_name, "domains": [container_name, container_hostname ]})

    traefik_hosts = set()
    for label, val in config["Labels"].items():
        if label == "traefik.frontend.rule":
            if val.startswith("Host:"):
                traefik_hosts.update(val[5:].strip().split(","))
            break

    return {"networks": networks, "traefik_hosts": traefik_hosts, "is_traefik": is_traefik}


def update_hosts_file():
    if len(hosts)==0:
        print("Removing all hosts before exit...")
    else:
        print("Updating hosts file with:")

    traefik_addr = ""
    traefik_hosts = set()
    for container in hosts.values():
        #collect all traefik hosts
        traefik_hosts |= container["traefik_hosts"]
        #take first traefik container ip address
        if container["is_traefik"] and not traefik_addr:
            for addr in container["networks"]:
                traefik_addr = addr["ip"]
                if traefik_addr:
                    break

    #read all the lines of thge original file
    lines = []
    with open(hosts_path,"r+") as hosts_file:
        lines = hosts_file.readlines()

    #remove all the lines after the known pattern
    for i,line in enumerate(lines):
        if line==enclosing_pattern:
            lines = lines[:i]
            break;

    #remove all the trailing newlines on the line list
    while lines[-1].strip()=="": lines.pop()

    end = len(lines)
    #append all the domain lines
    if len(hosts)>0:
        lines.append("\n\n"+enclosing_pattern)
        for id, container in hosts.items():
            #make non traefik hosts point to its own container
            for addr in container["networks"]:
                domains = addr["domains"]
                if traefik_addr:
                    # remove traefik hosts
                    domains -= traefik_hosts
                lines.append("%s    %s\n"%(addr["ip"],"   ".join(domains)))

        if traefik_addr and traefik_hosts:
            #make traefik hosts point to traefik container
            lines.append("\n#-----------Traefik-Hosts-----------\n")
            lines.append("%s    %s\n\n"%(traefik_addr,"   ".join(traefik_hosts)))

        lines.append("#-----Do-not-add-hosts-after-this-line-----\n\n")

    #write it on the auxiliar file
    aux_file_path = hosts_path+".aux"
    with open(aux_file_path,"w") as aux_hosts:
        aux_hosts.writelines(lines)

    #replace etc/hosts with aux file, making it atomic
    shutil.move(aux_file_path, hosts_path)
    print(''.join(lines[end:]))


def parse_args():
    parser = argparse.ArgumentParser(description='Synchronize running docker container IPs with host /etc/hosts file.')
    parser.add_argument('socket', type=str, nargs="?", default="tmp/docker.sock", help='The docker socket to listen for docker events.')
    parser.add_argument('file', type=str, nargs="?", default="/tmp/hosts", help='The /etc/hosts file to sync the containers with.')
    return parser.parse_args()

if __name__ == '__main__':
    main()

