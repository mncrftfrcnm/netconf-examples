"""Common parameter handling for netconf scripts."""

import os
import getpass
from dotenv import load_dotenv

load_dotenv()


def get_device_params(require_port: bool = False) -> dict:
    """Return connection params from env vars, falling back to interactive prompts."""
    ip = os.environ.get("NETCONF_IP") or input("Device IP: ").strip()
    username = os.environ.get("NETCONF_USERNAME") or input("Username: ").strip()
    password = os.environ.get("NETCONF_PASSWORD") or getpass.getpass("Password: ")
    port = int(os.environ.get("NETCONF_PORT", "830"))

    params = {
        "host": ip,
        "port": port,
        "username": username,
        "password": password,
        "hostkey_verify": False,
        "device_params": {"name": "iosxe"},
    }

    return params
