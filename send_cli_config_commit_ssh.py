#!/usr/bin/env python3
"""Send raw IOS CLI config with manual commit/confirm flow to Cisco IOS-XE via SSH only.

Flow:
  1. SSH/SFTP — upload commands file to flash:
  2. SSH      — backup running-config to flash: (rollback point)
  3. SSH      — schedule "reload in HH:MM" as a hardware-level safety net
               (fires if the new config breaks connectivity)
  4. SSH      — copy flash:<commands> into running-config
  5. Prompt   — user must confirm within CONFIRM_TIMEOUT seconds
  6a. Confirmed          — cancel reload, save config, cleanup, done
  6b. Timeout / No / ^C — cancel reload, restore backup (rollback), cleanup, exit 1

Usage:
    python send_cli_config_commit_ssh.py [commands_file]

Environment variables (or .env file):
    NETCONF_IP, NETCONF_USERNAME, NETCONF_PASSWORD
    NETCONF_CONFIG_FILE     - path to the commands file
    NETCONF_CONFIRM_TIMEOUT - seconds to wait for confirmation (default: 300)
"""

import os
import sys
import select
import tempfile
import time
import paramiko
from dotenv import load_dotenv
from jinja2 import Environment, StrictUndefined
from scp import SCPClient
from netconf_params import get_device_params

load_dotenv()

REMOTE_CONFIG_FILE = "netconf_cli_push.txt"
REMOTE_BACKUP_FILE = "netconf_cli_backup.cfg"
CONFIRM_TIMEOUT = int(os.environ.get("NETCONF_CONFIRM_TIMEOUT", "300"))

_CONFIRM_PROMPTS = ("[confirm]", "? [yes/no]", "filename [")


def get_commands_file() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    return os.environ.get("NETCONF_CONFIG_FILE") or input("Commands file path: ").strip()


def render_template(path: str) -> tuple[str, bool]:
    """Render path as a Jinja2 template using env vars as context.

    Returns (upload_path, is_temp). If is_temp is True the caller must delete
    the file after use.
    """
    with open(path) as f:
        source = f.read()
    rendered = Environment(undefined=StrictUndefined, keep_trailing_newline=True).from_string(source).render(**os.environ)
    if rendered == source:
        return path, False
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    tmp.write(rendered)
    tmp.close()
    print(f"Template rendered to temporary file: {tmp.name}")
    return tmp.name, True


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
    """Open a shell channel, send a command, auto-confirm any prompts, return output."""
    ssh = _new_ssh(host, username, password)
    try:
        shell = ssh.invoke_shell()
        time.sleep(1)
        shell.recv(65535)  # drain banner / prompt
        shell.send(command + "\n")
        time.sleep(2)
        output = shell.recv(65535).decode(errors="replace")
        for _ in range(5):
            if any(p in output for p in _CONFIRM_PROMPTS):
                shell.send("\n")
                time.sleep(1)
                output += shell.recv(65535).decode(errors="replace")
            else:
                break
        return output
    finally:
        ssh.close()


def ssh_copy(host: str, username: str, password: str, src: str, dst: str) -> None:
    _ssh_exec(host, username, password, f"copy {src} {dst}")
    print(f"  copy {src} {dst}: done")


def ssh_delete(host: str, username: str, password: str, filename: str) -> None:
    _ssh_exec(host, username, password, f"delete /force flash:{filename}")


def schedule_reload(host: str, username: str, password: str, timeout_seconds: int) -> None:
    minutes = max(1, -(-timeout_seconds // 60))  # ceiling division
    reload_time = f"{minutes // 60}:{minutes % 60:02d}"
    _ssh_exec(host, username, password, f"reload in {reload_time}")
    print(f"Reload scheduled in {reload_time} (hh:mm) as safety net.")


def cancel_reload(host: str, username: str, password: str) -> None:
    _ssh_exec(host, username, password, "reload cancel")
    print("Scheduled reload cancelled.")


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


def rollback(host: str, username: str, password: str) -> None:
    print("Rolling back to saved backup ...")
    ssh_copy(host, username, password, f"flash:{REMOTE_BACKUP_FILE}", "running-config")
    print("Rollback complete.")


def save_config(host: str, username: str, password: str) -> None:
    print("Saving configuration ...")
    _ssh_exec(host, username, password, "write memory")
    print("Configuration saved.")


def cleanup(host: str, username: str, password: str) -> None:
    print("Cleaning up temp files from flash: ...")
    ssh_delete(host, username, password, REMOTE_CONFIG_FILE)
    ssh_delete(host, username, password, REMOTE_BACKUP_FILE)
    print("Done.")


if __name__ == "__main__":
    commands_file = get_commands_file()
    params = get_device_params()
    host, username, password = params["host"], params["username"], params["password"]

    upload_path, is_temp = render_template(commands_file)
    try:
        print(f"\nUploading {commands_file!r} to {host}:flash:{REMOTE_CONFIG_FILE} ...")
        upload_file(host, username, password, upload_path)
        print("Upload complete.")
    finally:
        if is_temp:
            os.unlink(upload_path)

    print(f"\nBacking up running-config to flash:{REMOTE_BACKUP_FILE} ...")
    ssh_copy(host, username, password, "running-config", f"flash:{REMOTE_BACKUP_FILE}")
    print("Backup done.")

    schedule_reload(host, username, password, CONFIRM_TIMEOUT)

    print(f"\nApplying flash:{REMOTE_CONFIG_FILE} to running-config ...")
    ssh_copy(host, username, password, f"flash:{REMOTE_CONFIG_FILE}", "running-config")

    try:
        confirmed = confirm_with_timeout(CONFIRM_TIMEOUT)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        confirmed = False

    cancel_reload(host, username, password)

    if confirmed:
        print("\nConfirmed.")
        save_config(host, username, password)
        cleanup(host, username, password)
    else:
        rollback(host, username, password)
        cleanup(host, username, password)
        sys.exit(1)
