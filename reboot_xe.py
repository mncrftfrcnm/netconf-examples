#!/usr/bin/env python3
"""Reboot a Cisco IOS-XE device via NETCONF."""

from ncclient import manager
from ncclient.xml_ import to_ele
from netconf_params import get_device_params

# IOS-XE system restart RPC (YANG: Cisco-IOS-XE-rpc)
RELOAD_RPC = """
<reload xmlns="http://cisco.com/ns/yang/Cisco-IOS-XE-rpc">
  <force>true</force>
</reload>
"""


def reboot_device(params: dict) -> None:
    print(f"Connecting to {params['host']}:{params['port']} ...")
    with manager.connect(**params) as m:
        print("Connected. Sending reload RPC ...")
        try:
            response = m.dispatch(to_ele(RELOAD_RPC))
            print("Reload RPC sent successfully.")
            print(response)
        except Exception as e:
            # Device may drop the connection immediately after accepting the reload
            if "eof" in str(e).lower() or "closed" in str(e).lower():
                print("Connection closed by device — reload accepted.")
            else:
                raise


if __name__ == "__main__":
    params = get_device_params()
    reboot_device(params)
