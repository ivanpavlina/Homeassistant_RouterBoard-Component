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

Component is creating sensor per host/network specified. Every sensor has state Available or Unavailable and attributes contain actual traffic data.

#### Host sensor
States: On / Off 
- depends on status value of lease on dhcp-server - matching string 'bound'
- I would recommend using lower lease time in dhcp server so offline devices will switch to 'Off' more precisely.

#### Network sensor
State: Number of currently active hosts in network

#### Queue switch
State: Queue enabled
Attributes: Target, set bandwidth limit for queue


