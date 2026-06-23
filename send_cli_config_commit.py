#!/usr/bin/env python3
"""Send raw IOS CLI config with manual commit/confirm flow to Cisco IOS-XE.

The device does not route raw CLI through the candidate datastore, so the
standard NETCONF confirmed-commit cannot protect these changes. The flow is
implemented manually instead:

  1. SCP     — upload commands file to flash:
  2. NETCONF — backup running-config to flash: (rollback point)
  3. SSH     — schedule "reload in HH:MM" as a hardware-level safety net
               (fires if the new config breaks the NETCONF session itself)
  4. NETCONF — merge flash:<commands> into running-config
  5. Prompt  — user must confirm within CONFIRM_TIMEOUT seconds
  6a. Confirmed          — cancel reload, cleanup, done
  6b. Timeout / No / ^C — cancel reload, restore backup (rollback), cleanup, exit 1

Usage:
    python send_cli_config_commit.py [commands_file]

Environment variables (or .env file):
    NETCONF_IP, NETCONF_USERNAME, NETCONF_PASSWORD, NETCONF_PORT
    NETCONF_CONFIG_FILE     — path to the commands file
    NETCONF_CONFIRM_TIMEOUT — seconds to wait for confirmation (default: 300)
"""

import os
import sys
import select
import time
import paramiko
from dotenv import load_dotenv
from scp import SCPClient
from ncclient import manager
from ncclient.xml_ import to_ele
from netconf_params import get_device_params

load_dotenv()

REMOTE_CONFIG_FILE = "netconf_cli_push.txt"
REMOTE_BACKUP_FILE = "netconf_cli_backup.cfg"
CONFIRM_TIMEOUT = int(os.environ.get("NETCONF_CONFIRM_TIMEOUT", "300"))

COPY_RPC_TEMPLATE = """
<copy xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
  <source-drop-node-name>{src}</source-drop-node-name>
  <destination-drop-node-name>{dst}</destination-drop-node-name>
</copy>
"""

DELETE_RPC_TEMPLATE = """
<delete xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
  <filename-drop-node-name>flash:{filename}</filename-drop-node-name>
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
    ssh = _new_ssh(host, username, password)
    try:
        sftp = ssh.open_sftp()
        sftp.put(local_path, REMOTE_CONFIG_FILE)
        sftp.close()
        print("Uploaded via SFTP.")
        return
    except Exception as e:
        print(f"SFTP unavailable ({e}), trying SCP ...")
    finally:
        ssh.close()

    ssh = _new_ssh(host, username, password)
    try:
        with SCPClient(ssh.get_transport()) as scp:
            scp.put(local_path, REMOTE_CONFIG_FILE)
        print("Uploaded via SCP.")
    finally:
        ssh.close()


def _ssh_exec(host: str, username: str, password: str, command: str) -> str:
    """Open a shell channel, send a command, confirm if prompted, return output."""
    ssh = _new_ssh(host, username, password)
    try:
        shell = ssh.invoke_shell()
        time.sleep(1)
        shell.recv(65535)  # drain banner / prompt
        shell.send(command + "\n")
        time.sleep(1)
        output = shell.recv(65535).decode(errors="replace")
        if "[confirm]" in output:
            shell.send("\n")
            time.sleep(1)
            output += shell.recv(65535).decode(errors="replace")
        return output
    finally:
        ssh.close()


def schedule_reload(host: str, username: str, password: str, timeout_seconds: int) -> None:
    minutes = max(1, -(-timeout_seconds // 60))  # ceiling division
    reload_time = f"{minutes // 60}:{minutes % 60:02d}"
    output = _ssh_exec(host, username, password, f"reload in {reload_time}")
    print(f"Reload scheduled in {reload_time} (hh:mm) as safety net.")


def cancel_reload(host: str, username: str, password: str) -> None:
    _ssh_exec(host, username, password, "reload cancel")
    print("Scheduled reload cancelled.")


def netconf_copy(m: manager.Manager, src: str, dst: str) -> None:
    for attempt in range(3):
        try:
            m.dispatch(to_ele(COPY_RPC_TEMPLATE.format(src=src, dst=dst)))
            return
        except Exception:
            if attempt < 2:
                time.sleep(2)
            else:
                raise


def netconf_delete(m: manager.Manager, filename: str) -> None:
    for attempt in range(3):
        try:
            m.dispatch(to_ele(DELETE_RPC_TEMPLATE.format(filename=filename)))
            return
        except Exception:
            if attempt < 2:
                time.sleep(2)


def confirm_with_timeout(timeout: int) -> bool:
    """Prompt for confirmation. Returns True only if user types y/yes within timeout."""
    print(f"\nConfiguration applied. Auto-rollback in {timeout}s if not confirmed.")
    print("Confirm changes? [y/N]: ", end="", flush=True)
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if ready:
        answer = sys.stdin.readline().strip()
        return answer.lower() in ("y", "yes")
    print()
    return False


def rollback(m: manager.Manager) -> None:
    print("Rolling back to saved backup ...")
    netconf_copy(m, f"flash:{REMOTE_BACKUP_FILE}", "running-config")
    print("Rollback complete.")


def cleanup(m: manager.Manager) -> None:
    print("Cleaning up temp files from flash: ...")
    netconf_delete(m, REMOTE_CONFIG_FILE)
    netconf_delete(m, REMOTE_BACKUP_FILE)
    print("Done.")


if __name__ == "__main__":
    commands_file = get_commands_file()
    params = get_device_params()

    print(f"\nUploading {commands_file!r} to {params['host']}:flash:{REMOTE_CONFIG_FILE} ...")
    upload_file(params["host"], params["username"], params["password"], commands_file)
    print("Upload complete.")

    with manager.connect(**params) as m:
        print(f"\nBacking up running-config to flash:{REMOTE_BACKUP_FILE} ...")
        netconf_copy(m, "running-config", f"flash:{REMOTE_BACKUP_FILE}")
        print("Backup done.")

        schedule_reload(params["host"], params["username"], params["password"], CONFIRM_TIMEOUT)

        print(f"\nApplying flash:{REMOTE_CONFIG_FILE} to running-config ...")
        netconf_copy(m, f"flash:{REMOTE_CONFIG_FILE}", "running-config")

        try:
            confirmed = confirm_with_timeout(CONFIRM_TIMEOUT)
        except KeyboardInterrupt:
            print("\nInterrupted.")
            confirmed = False

        cancel_reload(params["host"], params["username"], params["password"])

        if confirmed:
            print("\nConfirmed.")
            cleanup(m)
        else:
            rollback(m)
            cleanup(m)
            sys.exit(1)
