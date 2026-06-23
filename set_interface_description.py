#!/usr/bin/env python3
"""Set an interface description on a Cisco IOS-XE device via NETCONF."""

import os
from ncclient import manager
from ncclient.xml_ import to_ele
from netconf_params import get_device_params

CONFIG_TEMPLATE = """
<config>
  <interfaces xmlns="urn:ietf:params:xml:ns:yang:ietf-interfaces">
    <interface>
      <name>{interface}</name>
      <description>{description}</description>
      <type xmlns:ianaift="urn:ietf:params:xml:ns:yang:iana-if-type">{if_type}</type>
      <enabled>true</enabled>
    </interface>
  </interfaces>
</config>
"""

_IF_TYPE_MAP = [
    ("Loopback",        "ianaift:softwareLoopback"),
    ("Tunnel",          "ianaift:tunnel"),
    ("Vlan",            "ianaift:l3ipvlan"),
    ("GigabitEthernet", "ianaift:ethernetCsmacd"),
    ("TenGigabitEthernet", "ianaift:ethernetCsmacd"),
    ("FastEthernet",    "ianaift:ethernetCsmacd"),
]


def _detect_if_type(interface: str) -> str:
    for prefix, yang_type in _IF_TYPE_MAP:
        if interface.startswith(prefix):
            return yang_type
    return "ianaift:other"


def get_interface_params() -> tuple[str, str]:
    interface = os.environ.get("NETCONF_INTERFACE") or input("Interface (e.g. GigabitEthernet1): ").strip()
    description = os.environ.get("NETCONF_DESCRIPTION") or input("Description: ").strip()
    return interface, description


def set_description(params: dict, interface: str, description: str) -> None:
    print(f"Connecting to {params['host']}:{params['port']} ...")
    with manager.connect(**params) as m:
        print(f"Connected. Setting description on {interface} ...")
        if_type = _detect_if_type(interface)
        config = CONFIG_TEMPLATE.format(interface=interface, description=description, if_type=if_type)
        m.edit_config(target="running", config=to_ele(config))
        print(f"Done. Interface {interface} description set to: {description!r}")


if __name__ == "__main__":
    params = get_device_params()
    interface, description = get_interface_params()
    set_description(params, interface, description)
