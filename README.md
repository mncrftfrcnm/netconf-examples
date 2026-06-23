# netconf_tests

Python scripts for configuring Cisco IOS-XE devices via NETCONF.

## Requirements

```
pip install ncclient paramiko scp python-dotenv jinja2
```

## Required Open Ports

| Port | Protocol | Direction | Purpose |
|------|----------|-----------|---------|
| 22   | TCP      | Inbound (device) | SSH � used for SCP/SFTP file transfer |
| 830  | TCP      | Inbound (device) | NETCONF over SSH |

Ensure your firewall/ACL allows the management host to reach the device on both ports.

## Cisco IOS-XE Configuration requirements

```
hostname {{ inventory_hostname }}
aaa new-model
aaa authentication login default local
aaa authorization exec default local
username {{ NETCONF_USERNAME }} privilege 15 secret {{ NETCONF_PASSWORD }}
ip ssh version 2
ip scp server enable
netconf-yang
netconf-yang feature candidate-datastore
line vty 0 4
 transport input ssh
end
```

## Configuration

Copy `.env.example` to `.env` and fill in your device credentials:

```
cp .env.example .env
```

All scripts read connection parameters from environment variables or `.env`,
falling back to interactive prompts if not set.

## Scripts

### `set_interface_description.py`

Set a description on a specific interface.

```bash
python set_interface_description.py
```

Relevant env vars: `NETCONF_INTERFACE`, `NETCONF_DESCRIPTION`

---

### `reboot_xe.py`

Reboot the device via NETCONF (`reload` RPC).

```bash
python reboot_xe.py
```

---

### `send_cli_config.py`

Send a plain-text IOS config file to the device.

Delivery flow:
1. Upload the file to `flash:` via SCP (falls back to SFTP)
2. Apply it to `running-config` via NETCONF `copy` RPC
3. Delete the temp file from `flash:`

```bash
python send_cli_config.py commands.txt
```

Relevant env vars: `NETCONF_CONFIG_FILE`

---

### `send_cli_config_commit.py`

Same as `send_cli_config.py` but with a manual commit/confirm safety flow:

1. Upload the file to `flash:`
2. Back up `running-config` to `flash:` (rollback point)
3. Schedule `reload in HH:MM` as a hardware-level safety net —
   if the new config breaks the NETCONF session, the device reloads
   automatically and boots from `startup-config`
4. Apply the config to `running-config`
5. Prompt for confirmation within `NETCONF_CONFIRM_TIMEOUT` seconds
   - **Confirmed** — cancel reload, save configuration (`write memory`), clean up temp files
   - **Timeout / No / Ctrl-C** — cancel reload, restore backup, clean up, exit 1

```bash
python send_cli_config_commit.py commands.txt
```

Relevant env vars: `NETCONF_CONFIG_FILE`, `NETCONF_CONFIRM_TIMEOUT`

---

### `send_cli_config_commit_ssh.py`

Same commit/confirm flow as `send_cli_config_commit.py` but **SSH only** — no NETCONF required.
All file operations (`copy`, `delete`) are executed via SSH shell commands instead of NETCONF RPCs.
Only port 22 needs to be open; port 830 is not used.

1. Upload the file to `flash:` via SCP (falls back to SFTP)
2. Back up `running-config` to `flash:` via SSH `copy`
3. Schedule `reload in HH:MM` as a hardware-level safety net
4. Apply the config to `running-config` via SSH `copy`
5. Prompt for confirmation within `NETCONF_CONFIRM_TIMEOUT` seconds
   - **Confirmed** — cancel reload, save configuration (`write memory`), clean up temp files
   - **Timeout / No / Ctrl-C** — cancel reload, restore backup, clean up, exit 1

```bash
python send_cli_config_commit_ssh.py commands.txt
```

Relevant env vars: `NETCONF_CONFIG_FILE`, `NETCONF_CONFIRM_TIMEOUT`

## Commands file format

See `commands.txt.example`. Plain IOS config lines, same as copy/paste
into config mode. No YANG mapping required.

The commands file can also be a **Jinja2 template**. All environment variables
(including those from `.env`) are available as template variables:

```
interface GigabitEthernet1
 description {{ UPLINK_DESC }}
!
interface GigabitEthernet2
 description {{ LAN_DESC }}
!
```

```bash
UPLINK_DESC="Uplink to Core" LAN_DESC="LAN Access" python send_cli_config_commit_ssh.py commands_template.txt
```

The rendered file is uploaded; the original template is never sent to the device.
Missing variables cause an error before any changes are made.

## Environment variables

| Variable | Description | Default |
|---|---|---|
| `NETCONF_IP` | Device IP address | prompted |
| `NETCONF_USERNAME` | SSH/NETCONF username | prompted |
| `NETCONF_PASSWORD` | SSH/NETCONF password | prompted |
| `NETCONF_PORT` | NETCONF port | `830` |
| `NETCONF_INTERFACE` | Interface name (`set_interface_description.py`) | prompted |
| `NETCONF_DESCRIPTION` | Interface description (`set_interface_description.py`) | prompted |
| `NETCONF_CONFIG_FILE` | Path to commands file | prompted |
| `NETCONF_CONFIRM_TIMEOUT` | Confirm timeout in seconds (`send_cli_config_commit.py`) | `300` |
