# Homeassistant_RouterBoard-Component
Custom RouterBoard component for Homeassistant

## Requirements
Routerboard's accounting service must be enabled.
Run this commands on Routerboard terminal
> /ip accounting set enabled=yes account-local-traffic=yes

account-local-traffic option is optional, component can handle both states

https://wiki.mikrotik.com/wiki/Manual:IP/Accounting

Username to use while connecting to API, only 'read' rights required.
> /user add name=api_read password=api_read group=read disabled=no

## Usage
Add the following to your `configuration.yaml` file:

```
routerboard:
  host: 192.168.88.1
  username: api_read
  password: api_read
  scan_interval: 5
  traffic_unit: 'Mb/s'
  expand_network_hosts: true
  monitored_conditions:
    - 192.168.88.0/24
    - 192.168.99.20
    - 192.168.99.21
  custom_switches:
    - name: "RDP NAT"
      turn_on:
        cmd: "/ip/firewall/nat/enable"
        args: "=numbers=7,8"
      turn_off:
        cmd: "/ip/firewall/nat/disable"
        args: "=numbers=7,8"
      state:
        cmd: "/ip/firewall/nat/print"
        args: "?comment=MyPC-RDP"
```

Key | Type | Required | Default | Description
-- | -- | -- | -- | --
`host` | `string` | `True` | | Routerboard hostname/IP address.
`username` | `string` | `False` | api_read | Routerboard API username.
`password` | `string` | `False` | api_read | Routerboard API password.
`scan_interval` | `int` | `False` | 30 | Routerboard data pool interval
`traffic_unit` | `string` | `False` | `Mb/s` | Unit of mesurement for traffic attributes. Supported values [b/s, B/s, Kb/s, KB/s, Mb/s, MB/s]
`expand_network_hosts` | `bool` | `False` | `False` | If network specified in monitored conditions (ex. 192.168.88.0/24) also dinamicaly add all connected hosts inside the network.
`monitored_conditions` | `list` | `True` | | Specify address (ex. 192.168.88.123) or networks (ex. 192.168.88.0/24) (or mixed!) to track network throughput. 
`manage_queues` | `bool` | `False` | `False` | If enabled all queues inside mikrotik will be exposed as switches with ability to turn them on and off. Switches attributes display current bandwidth limit specified  
`custom_switches` | `list` | `False` | | List of custom switches which can execute custom API calls. See below for actual switch configuration
Component is creating sensor per host/network specified. Every sensor has state Available or Unavailable and attributes contain actual traffic data.

#### Host sensor
States: On / Off 
- depends on status value of lease on dhcp-server - matching string 'bound'
- I would recommend using lower lease time in DHCP server so offline devices will switch to 'Off' more precisely.

#### Network sensor
State: Number of currently active hosts in network

#### Queue switch
State: Queue enabled
Attributes: Target, set bandwidth limit for queue

#### Custom switch
Required objects : `turn_on`, `turn_off`, `state`
Every object has to contain `cmd` and `args` objects which will be executed on mikrotik API.

#### Services
Service name : `routerboard.run_script`
Runs predefined mikrotik script

Parameter | Description | Example
name | RouterBoard | MyScript

