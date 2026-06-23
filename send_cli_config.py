#!/usr/bin/env python3
"""Send raw IOS CLI config file to a Cisco IOS-XE device.

Delivery flow:
  1. SCP  — upload commands file to flash: on the device
  2. NETCONF copy RPC — merge flash:<file> into running-config
  3. NETCONF delete RPC — remove the temp file from flash:

The commands file is a plain IOS config text file (same format as
copy/paste into config mode).  No YANG mapping required.

Usage:
    python send_cli_config.py [commands_file]

Environment variables (or .env file):
    NETCONF_IP, NETCONF_USERNAME, NETCONF_PASSWORD, NETCONF_PORT
    NETCONF_CONFIG_FILE   — path to the commands file
"""

import os
import sys
import paramiko
from scp import SCPClient
from ncclient import manager
from ncclient.xml_ import to_ele
from netconf_params import get_device_params

REMOTE_FILENAME = "netconf_cli_push.txt"

COPY_RPC = f"""
<copy xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
  <source-drop-node-name>flash:{REMOTE_FILENAME}</source-drop-node-name>
  <destination-drop-node-name>running-config</destination-drop-node-name>
</copy>
"""

DELETE_RPC = f"""
<delete xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
  <filename-drop-node-name>flash:{REMOTE_FILENAME}</filename-drop-node-name>
</delete>
"""


def get_commands_file() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    return os.environ.get("NETCONF_CONFIG_FILE") or input("Commands file path: ").strip()


def _new_ssh(host: str, username: str, password: str) -> paramiko.SSHClient:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=22, username=username, password=password)
    return ssh


def upload_file(host: str, username: str, password: str, local_path: str) -> None:
    # Try SFTP first
    ssh = _new_ssh(host, username, password)
    try:
        sftp = ssh.open_sftp()
        sftp.put(local_path, REMOTE_FILENAME)
        sftp.close()
        print("Uploaded via SFTP.")
        return
    except Exception as e:
        print(f"SFTP unavailable ({e}), trying SCP ...")
    finally:
        ssh.close()

    # Fresh connection for SCP
    ssh = _new_ssh(host, username, password)
    try:
        with SCPClient(ssh.get_transport()) as scp:
            scp.put(local_path, REMOTE_FILENAME)
        print("Uploaded via SCP.")
    finally:
        ssh.close()


def apply_config(params: dict) -> None:
    with manager.connect(**params) as m:
        print(f"Applying flash:{REMOTE_FILENAME} to running-config ...")
        response = m.dispatch(to_ele(COPY_RPC))
        print("Configuration applied.")
        print(response)

        print(f"Deleting flash:{REMOTE_FILENAME} ...")
        for attempt in range(3):
            try:
                m.dispatch(to_ele(DELETE_RPC))
                print("Temp file removed.")
                break
            except Exception as e:
                if attempt < 2:
                    import time; time.sleep(2)
                else:
                    print(f"Warning: could not delete temp file: {e}")


if __name__ == "__main__":
    commands_file = get_commands_file()
    params = get_device_params()

    print(f"Uploading {commands_file!r} to {params['host']}:flash:{REMOTE_FILENAME} ...")
    upload_file(params["host"], params["username"], params["password"], commands_file)
    print("Upload complete.")

    apply_config(params)
