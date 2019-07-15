"""Support for the RouterBoard client API."""
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_USERNAME, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL, CONF_MONITORED_CONDITIONS)
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import track_time_interval
import ipaddress

_LOGGER = logging.getLogger(__name__)

DOMAIN = 'routerboard'
DATA_UPDATED = 'routerboard_data_updated'
DATA_ROUTERBOARD = 'data_routerboard'

DEFAULT_NAME = 'RouterBoard'
DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = ''
DEFAULT_PORT = 8728

AVAILABLE_TRAFFIC_UNITS = ['b/s', 'B/s', 'Kb/s', 'KB/s', 'Mb/s', 'MB/s']

CONF_TRAFFIC_UNIT = 'traffic_unit'
CONF_EXPAND_NETWORK_HOSTS = 'expand_network_hosts'

DEFAULT_TRAFFIC_UNIT = 'Mb/s'

DEFAULT_SCAN_INTERVAL = timedelta(seconds=15)

CONFIG_SCHEMA = vol.Schema({
    DOMAIN: vol.Schema({
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): cv.string,
        vol.Optional(CONF_PASSWORD, default=DEFAULT_PASSWORD): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): cv.time_period,
        vol.Optional(CONF_TRAFFIC_UNIT, default=DEFAULT_TRAFFIC_UNIT): vol.In(AVAILABLE_TRAFFIC_UNITS),
        vol.Optional(CONF_EXPAND_NETWORK_HOSTS, default=False): cv.boolean,
        vol.Optional(CONF_MONITORED_CONDITIONS, default=[]): vol.All(),
    })
}, extra=vol.ALLOW_EXTRA)


def setup(hass, config):
    """Set up the RouterBoard Component."""
    host = config[DOMAIN][CONF_HOST]
    username = config[DOMAIN].get(CONF_USERNAME)
    password = config[DOMAIN].get(CONF_PASSWORD)
    port = config[DOMAIN][CONF_PORT]
    scan_interval = config[DOMAIN][CONF_SCAN_INTERVAL]
    monitored_conditions = config[DOMAIN][CONF_MONITORED_CONDITIONS]
    traffic_unit = config[DOMAIN][CONF_TRAFFIC_UNIT]
    expand_network_hosts = config[DOMAIN][CONF_EXPAND_NETWORK_HOSTS]

    _LOGGER.debug(f"""
    Configuration:
      Host: {host},
      Username: {username},
      Password: {password},
      Port: {port},
      Scan interval: {scan_interval},
      Monitored conditions: {monitored_conditions},
      Traffic unit: {traffic_unit}
      Expand network hosts: {expand_network_hosts}""")

    from librouteros import connect
    from librouteros.exceptions import ConnectionError, LoginError
    try:
        api = connect(host=host, port=port, username=username, password=password)
        # Returns None if auth fails (could be wrong)
        if not api:
            raise ConnectionError()
        _LOGGER.info("Connected to API")
    except ConnectionError:
        _LOGGER.error("Could not establish connection to RouterBoard API")
        return False
    except LoginError:
        _LOGGER.error("Invalid credentials")
        return False
    except Exception as e:
        _LOGGER.error(f"Unknown exception occurred while connecting to RouterBoard API - {type(e)}/{e.args}")
        return False

    try:
        rb_data = hass.data[DATA_ROUTERBOARD] = RouterBoardData(hass, api, traffic_unit)
    except LookupError:
        _LOGGER.error("Accounting not active in RouterBoard, "
                      "please enable it and restart HomeAssistant "
                      "(https://wiki.mikrotik.com/wiki/Manual:IP/Accounting)")
        return False
    rb_data.update()

    def refresh(event_time):
        """Get the latest data from RouterBoard."""
        rb_data.update()

    track_time_interval(hass, refresh, scan_interval)

    sensorconfig = {
        'sensors': config[DOMAIN][CONF_MONITORED_CONDITIONS],
        'client_name': config[DOMAIN][CONF_NAME],
        'expand_network': config[DOMAIN][CONF_EXPAND_NETWORK_HOSTS]}

    discovery.load_platform(hass, 'sensor', DOMAIN, sensorconfig, config)

    return True


class RouterBoardData:
    """Get the latest data and update the states."""

    def __init__(self, hass, api, traffic_unit):
        """Initialize the data handler."""
        self._api = api
        self._hass = hass
        self.traffic_unit = traffic_unit

        self._local_networks = []
        self._hosts = {}
        self._latest_bytes_count = {}
        self._latest_packets_count = {}
        self._last_run = 0
        self._last_interval = None

        self.available = True

        if not self._is_ip_accounting_enabled():
            raise LookupError

        self.init_local_networks()
        # Hit snapshot on init to clear previous accounting data
        self._take_accounting_snapshot()

    def _is_ip_accounting_enabled(self):
        return self._api(cmd="/ip/accounting/print")[0].get('enabled') == 'yes'

    def get_all_hosts_from_network(self, network):
        return [x for x in self._hosts.keys() if ipaddress.IPv4Address(x) in ipaddress.IPv4Network(network)]

    def host_exists(self, host):
        return self._hosts.get(host) is not None

    @staticmethod
    def __current_milliseconds():
        from time import time
        return int(round(time() * 1000))

    def _is_address_part_of_local_network(self, address):
        for network in self._local_networks:
            if address in network:
                return True
        return False

    def _take_accounting_snapshot(self):
        # Take snapshot of all captured packets
        # Returns interval in seconds between last snapshot take
        self._api(cmd="/ip/accounting/snapshot/take")
        current_time = self.__current_milliseconds()
        interval = current_time - self._last_run
        self._last_run = current_time
        return interval / 1000

    def init_local_networks(self):
        dhcp_networks = self._api(cmd="/ip/dhcp-server/network/print")
        self._local_networks = [ipaddress.IPv4Network(network.get('address')) for network in dhcp_networks]
        _LOGGER.info(f"Local networks initialized - {self._local_networks}")

    def update(self):
        """Get the latest data from Routerboard instance."""
        from librouteros.exceptions import ConnectionError

        try:
            # Get all hosts from DHCP leases, build host dict and collapse all addresses to common network
            dhcp_leases = self._api(cmd="/ip/dhcp-server/lease/print")

            self._hosts = {lease.get('address'): lease for lease in dhcp_leases}
            _LOGGER.debug(f"Retrieved {len(self._hosts)} hosts")

            # Take accounting snapshot and retrieve the data
            self._last_interval = self._take_accounting_snapshot()
            _LOGGER.debug(f"Time between snapshots is {self._last_interval} seconds")
            traffic_list = self._api(cmd="/ip/accounting/snapshot/print")

            result_bytespersecond = {}
            result_packetspersecond = {}
            for traffic in traffic_list:
                source_ip = ipaddress.ip_address(str(traffic.get('src-address')).strip())
                destination_ip = ipaddress.ip_address(str(traffic.get('dst-address')).strip())

                bytes_count = int(str(traffic.get('bytes')).strip())
                packets_count = int(str(traffic.get('packets')).strip())

                # TODO If traffic is local both destination and source traffic should be counted.
                #  ATM it's only counting data of source traffic, destination is ignored
                if self._is_address_part_of_local_network(source_ip) and self._is_address_part_of_local_network(destination_ip):
                    traffic_type = 'local'
                    local_ip = str(source_ip)
                elif self._is_address_part_of_local_network(source_ip) and not self._is_address_part_of_local_network(destination_ip):
                    traffic_type = 'upload'
                    local_ip = str(source_ip)
                elif not self._is_address_part_of_local_network(source_ip) and self._is_address_part_of_local_network(destination_ip):
                    traffic_type = 'download'
                    local_ip = str(destination_ip)
                else:
                    _LOGGER.debug(f"Skipping packet from {source_ip} to {destination_ip}")
                    continue

                if local_ip not in result_bytespersecond:
                    result_bytespersecond[local_ip] = {}
                if local_ip not in result_packetspersecond:
                    result_packetspersecond[local_ip] = {}

                if traffic_type not in result_bytespersecond[local_ip]:
                    result_bytespersecond[local_ip][traffic_type] = bytes_count
                else:
                    result_bytespersecond[local_ip][traffic_type] += bytes_count

                if traffic_type not in result_packetspersecond[local_ip]:
                    result_packetspersecond[local_ip][traffic_type] = packets_count
                else:
                    result_packetspersecond[local_ip][traffic_type] += packets_count

            self._latest_bytes_count = result_bytespersecond
            self._latest_packets_count = result_packetspersecond
            dispatcher_send(self._hass, DATA_UPDATED)

            _LOGGER.debug(f"Traffic data updated, {len(traffic_list)} rows processed")
            self.available = True
        except ConnectionError:
            self.available = False
            _LOGGER.error("Unable to connect to Routerboard API")

    def get_address_name(self, address):
        try:
            host = self._hosts[address]
            return host.get('comment') if host.get('comment') else host.get('host-name')
        except:
            return address

    def get_address_mac(self, address):
        try:
            return self._hosts[address].get('mac-address')
        except:
            return '00:00:00:00:00:00'

    def _convert_bytes_to_requested_unit(self, bytes_count):
        converted = bytes_count
        # Check if bits or bytes (bits if 'b' in first two letters of traffic unit)
        if 'b' in self.traffic_unit[:2]:
            converted = converted * 8

        # Handle Kilo and Mega
        if self.traffic_unit[:1].upper() == 'K':
            converted = round(converted / 1000, 1)
        elif self.traffic_unit[:1].upper() == 'M':
            converted = round(converted / 1000000, 2)

        return converted

    def get_address_active_state(self, address):
        try:
            return self._hosts[address].get('status') == 'bound'
        except:
            return False

    def get_address_traffic_value(self, address, traffic_type):
        try:
            bytes_per_second = round(self._latest_bytes_count[address][traffic_type] / self._last_interval)
            return self._convert_bytes_to_requested_unit(bytes_per_second)
        except:
            return 0

    def get_address_packet_value(self, address, traffic_type):
        try:
            return round(self._latest_packets_count[address][traffic_type] / self._last_interval)
        except:
            return 0

    def get_network_traffic_value(self, network, traffic_type):
        try:
            value = 0
            for host in self.get_all_hosts_from_network(network):
                if host in self._latest_bytes_count and traffic_type in self._latest_bytes_count[host]:
                    value += self._latest_bytes_count[host][traffic_type]

            bytes_per_second = round(value / self._last_interval)
            return self._convert_bytes_to_requested_unit(bytes_per_second)
        except:
            return 0

    def get_network_packet_value(self, network, traffic_type):
        try:
            value = 0
            for host in self.get_all_hosts_from_network(network):
                if host in self._latest_packets_count and traffic_type in self._latest_packets_count[host]:
                    value += self._latest_packets_count[host][traffic_type]

            return round(value / self._last_interval)
        except:
            return 0
