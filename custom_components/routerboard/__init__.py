"""RouterBoard client API."""
from datetime import timedelta
import logging
import ipaddress
import voluptuous as vol

from homeassistant.const import (
    CONF_HOST, CONF_NAME, CONF_USERNAME, CONF_PASSWORD, CONF_PORT, CONF_SCAN_INTERVAL, CONF_MONITORED_CONDITIONS)
from homeassistant.helpers import config_validation as cv, discovery
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import track_time_interval


_LOGGER = logging.getLogger(__name__)


def _is_address_a_network(address):
    try:
        ipaddress.IPv4Address(address)
        return False
    except ipaddress.AddressValueError:
        try:
            ipaddress.IPv4Network(address)
            return True
        except Exception:
            raise


DOMAIN = 'routerboard'
DATA_UPDATED = 'routerboard_data_updated'
DATA_ROUTERBOARD = 'data_routerboard'

DEFAULT_NAME = 'RouterBoard'
DEFAULT_USERNAME = 'api_read'
DEFAULT_PASSWORD = 'api_read'
DEFAULT_PORT = 8728

AVAILABLE_TRAFFIC_UNITS = ['b/s', 'B/s', 'Kb/s', 'KB/s', 'Mb/s', 'MB/s']
AVAILABLE_MONITORED_TRAFFIC = ['active', 'download', 'upload', 'local', 'wan']

CONF_TRAFFIC_UNIT = 'traffic_unit'
CONF_EXPAND_NETWORK_HOSTS = 'expand_network_hosts'
CONF_MONITORED_TRAFFIC = 'monitored_traffic'
CONF_MANAGE_QUEUES = 'manage_queues'
CONF_TRACK_ENV_VARIABLES = 'track_env_variables'

DEFAULT_TRAFFIC_UNIT = 'Mb/s'

DEFAULT_SCAN_INTERVAL = timedelta(seconds=30)
try:
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
            vol.Optional(CONF_MONITORED_TRAFFIC, default=['active']): vol.All(cv.ensure_list, [vol.In(AVAILABLE_MONITORED_TRAFFIC)]),
            vol.Optional(CONF_TRACK_ENV_VARIABLES, default=[]): vol.All(),
            vol.Optional(CONF_EXPAND_NETWORK_HOSTS, default=False): cv.boolean,
            vol.Optional(CONF_MONITORED_CONDITIONS, default=[]): vol.All(),
            vol.Optional(CONF_MANAGE_QUEUES, default=False): cv.boolean
        })
    }, extra=vol.ALLOW_EXTRA)
except Exception as e:
    _LOGGER.info(f"Exception while setting up input - {type(e)}  {e.args}")


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
    manage_queues = config[DOMAIN][CONF_MANAGE_QUEUES]
    track_env_variables = config[DOMAIN][CONF_TRACK_ENV_VARIABLES]

    _LOGGER.debug(f"""
    Configuration:
      Host: {host},
      Username: {username},
      Password: {password},
      Port: {port},
      Scan interval: {scan_interval},
      Monitored conditions: {monitored_conditions},
      Traffic unit: {traffic_unit},
      Expand network hosts: {expand_network_hosts},
      Manage queues: {manage_queues},
      Track env variables: {track_env_variables}""")

    from librouteros.exceptions import ConnectionError, LoginError

    try:

        rb_data = hass.data[DATA_ROUTERBOARD] = RouterBoardData(hass, host, port, username, password, traffic_unit)
        # Returns None if auth fails (could be wrong)

        _LOGGER.info("Connected to API")
    except ConnectionError:
        _LOGGER.error("Could not establish connection to RouterBoard API")
        return False
    except LoginError:
        _LOGGER.error("Invalid credentials")
        return False
    except LookupError:
        _LOGGER.error("Accounting not active in RouterBoard, "
                      "please enable it and restart HomeAssistant "
                      "(https://wiki.mikrotik.com/wiki/Manual:IP/Accounting)")
        return False
    except Exception as e:
        _LOGGER.error(f"Unknown exception occurred while connecting to RouterBoard API - {type(e)}/{e.args}")
        return False

    rb_data.update()

    def refresh(event_time):
        """Get the latest data from RouterBoard."""
        rb_data.update()

    track_time_interval(hass, refresh, scan_interval)

    sensor_config = {
        'conditions': config[DOMAIN][CONF_MONITORED_CONDITIONS],
        'client_name': config[DOMAIN][CONF_NAME],
        'expand_network': config[DOMAIN][CONF_EXPAND_NETWORK_HOSTS],
        'mon_traffic': config[DOMAIN][CONF_MONITORED_TRAFFIC],
        'track_env_variables': config[DOMAIN][CONF_TRACK_ENV_VARIABLES]}

    discovery.load_platform(hass, 'sensor', DOMAIN, sensor_config, config)

    if config[DOMAIN][CONF_MANAGE_QUEUES]:
        switch_config = {
            'client_name': config[DOMAIN][CONF_NAME]
        }
        discovery.load_platform(hass, 'switch', DOMAIN, switch_config, config)

    return True


class RouterBoardData:
    """Get the latest data and update the states."""

    def __init__(self, hass, host, port, username, password, traffic_unit):
        """Initialize the data handler."""
        self._hass = hass
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self.traffic_unit = traffic_unit

        self._api = None
        self._local_networks = []
        self._hosts = {}
        self._latest_bytes_count = {}
        self._latest_packets_count = {}
        self._queues = {}
        self._last_run = 0  # Milliseconds
        self._last_interval = 0  # Seconds
        self._variable_values = {}

        self.reconnect()

        self.available = True

        if not self._is_ip_accounting_enabled():
            raise LookupError

        self.init_local_networks()
        # Hit snapshot on init to clear previous accounting data
        self._take_accounting_snapshot()

    def reconnect(self):
        from librouteros import connect
        from librouteros.login import login_plain

        self._api = connect(host=self._host, port=self._port, username=self._username, password=self._password, login_methods=(login_plain, ))

    def _is_ip_accounting_enabled(self):
        return self._api(cmd="/ip/accounting/print")[0].get('enabled')

    def get_queue_list(self):
        return self._queues.keys()

    def get_queue_target(self, queue_id):
        return self._queues[queue_id].get('target')

    def get_queue_name(self, queue_id):
        return self._queues[queue_id].get('name')

    def get_queue_limits(self, queue_id):
        return [self._convert_bits_to_appropriate_unit(limit) for limit in self._queues[queue_id].get('max-limit').split('/')]

    def get_queue_state(self, queue_id):
        return self._queues[queue_id].get('invalid') is False and self._queues[queue_id].get('disabled') is False

    def set_queue_state(self, queue_id, state):
        params = {'.id': queue_id, 'disabled': not state}
        self._api(cmd="/queue/simple/set", **params)

    def get_all_hosts_from_network(self, network):
        return [x for x in self._hosts.keys() if ipaddress.IPv4Address(x) in ipaddress.IPv4Network(network)]

    def host_exists(self, host):
        return self._hosts.get(host) is not None

    def get_variable_value(self, variable):
        return self._variable_values.get(variable)

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
        # Takes snapshot of all captured packets and keeps timing of snapshots
        self._api(cmd="/ip/accounting/snapshot/take")
        current_time = self.__current_milliseconds()
        interval = current_time - self._last_run
        self._last_run = current_time
        self._last_interval = interval / 1000
        _LOGGER.debug(f"Time between snapshots is {self._last_interval} seconds")

    def init_local_networks(self):
        dhcp_networks = self._api(cmd="/ip/dhcp-server/network/print")
        self._local_networks = [ipaddress.IPv4Network(network.get('address')) for network in dhcp_networks]

    def _reset_byte_and_packet_counters(self):
        self._latest_bytes_count = {}
        self._latest_packets_count = {}

    def _update_byte_and_packet_counters(self, local_ip, traffic_type, bytes_count, packets_count):
        if bytes_count > 0:
            if local_ip not in self._latest_bytes_count:
                self._latest_bytes_count[local_ip] = {}
            if traffic_type not in self._latest_bytes_count[local_ip]:
                self._latest_bytes_count[local_ip][traffic_type] = bytes_count
            else:
                self._latest_bytes_count[local_ip][traffic_type] += bytes_count

        if packets_count > 0:
            if local_ip not in self._latest_packets_count:
                self._latest_packets_count[local_ip] = {}

            if traffic_type not in self._latest_packets_count[local_ip]:
                self._latest_packets_count[local_ip][traffic_type] = packets_count
            else:
                self._latest_packets_count[local_ip][traffic_type] += packets_count

    def update(self):
        """Get the latest data from Routerboard instance."""

        try:
            # Get all hosts from DHCP leases, build host dict and collapse all addresses to common network
            dhcp_leases = self._api(cmd="/ip/dhcp-server/lease/print")

            self._hosts = {lease.get('address'): lease for lease in dhcp_leases}
            _LOGGER.debug(f"Retrieved {len(self._hosts)} hosts")
            #self.available = True
        except Exception as e:
            #self.available = False
            _LOGGER.warning(f"Unable to retrieve hosts from dhcp leases - {type(e)} {e.args}")
            try:
                self.reconnect()
            except Exception as e:
                _LOGGER.warning(f"Error reconnecting API - {type(e)} {e.args}")

        try:
            # Take accounting snapshot and retrieve the data
            self._take_accounting_snapshot()
            traffic_list = self._api(cmd="/ip/accounting/snapshot/print")

            self._reset_byte_and_packet_counters()

            for traffic in traffic_list:
                source_ip = ipaddress.ip_address(str(traffic.get('src-address')).strip())
                destination_ip = ipaddress.ip_address(str(traffic.get('dst-address')).strip())

                bytes_count = int(str(traffic.get('bytes')).strip())
                packets_count = int(str(traffic.get('packets')).strip())

                if self._is_address_part_of_local_network(source_ip) and self._is_address_part_of_local_network(destination_ip):
                    # Local traffic
                    self._update_byte_and_packet_counters(str(source_ip), 'local', bytes_count, packets_count)
                    self._update_byte_and_packet_counters(str(destination_ip), 'local', bytes_count, packets_count)
                elif self._is_address_part_of_local_network(source_ip) and not self._is_address_part_of_local_network(destination_ip):
                    # Upload traffic
                    self._update_byte_and_packet_counters(str(source_ip), 'upload', bytes_count, packets_count)
                elif not self._is_address_part_of_local_network(source_ip) and self._is_address_part_of_local_network(destination_ip):
                    # Download traffic
                    self._update_byte_and_packet_counters(str(destination_ip), 'download', bytes_count, packets_count)
                else:
                    _LOGGER.debug(f"Skipping packet from {source_ip} to {destination_ip}")
                    continue

            _LOGGER.debug(f"Traffic data updated, {len(traffic_list)} rows processed")
            #self.available = True
        except Exception as e:
            #self.available = False
            _LOGGER.warning(f"Unable to retrieve accounting data - {type(e)} {e.args}")
            try:
                self.reconnect()
            except Exception as e:
                _LOGGER.warning(f"Error reconnecting API - {type(e)} {e.args}")

        try:
            # Get all queues
            queues = self._api(cmd="/queue/simple/print")
            self._queues = {queue.get('.id'): queue for queue in queues}
            _LOGGER.debug(f"Retrieved {len(self._queues)} queues")
            # self.available = True
        except Exception as ex:
            # self.available = False
            _LOGGER.warning(f"Unable to retrieve queues - {type(ex)} {ex.args}")
            try:
                self.reconnect()
            except Exception as e:
                _LOGGER.warning(f"Error reconnecting API - {type(e)} {e.args}")

        try:
            environment = self._api(cmd="/system/script/environment/print")
            for var in environment:
                self._variable_values[var.get('name')] = var.get('value')

        except Exception as ex1:
            # self.available = False
            _LOGGER.warning(f"Unable to retrieve environment - {type(ex1)} {ex1.args}")
            try:
                self.reconnect()
            except Exception as er:
                _LOGGER.warning(f"Error reconnecting API - {type(er)} {er.args}")

        dispatcher_send(self._hass, DATA_UPDATED)

    def get_address_name(self, address):
        try:
            host = self._hosts[address]
            return host.get('comment') or host.get('host-name') or host.get('mac-address')
        except:
            return address

    def get_address_mac(self, address):
        try:
            return self._hosts[address].get('mac-address')
        except:
            return '00:00:00:00:00:00'

    def _convert_bits_to_appropriate_unit(self, bits_count):
        converted = int(bits_count)
        unit = 'bits/s'
        if converted >= 1000000:
            converted = converted / 1000000
            unit = f'M{unit}'
        elif converted >= 1000:
            converted = converted / 1000
            unit = f'k{unit}'

        return f'{round(converted)}{unit}'

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

    def get_active_hosts_in_network(self, network):
        return [host for host in self.get_all_hosts_from_network(network) if self.host_is_active(host)]

    def host_is_active(self, address):
        try:
            return self._hosts[address].get('status') == 'bound'
        except:
            return False

    def get_address_traffic_value(self, address, traffic_type):
        if self.host_is_active(address):
            try:
                bytes_per_second = round(self._latest_bytes_count[address][traffic_type] / self._last_interval)
                return self._convert_bytes_to_requested_unit(bytes_per_second)
            except:
                return 0
        return -1
        #bytes_per_second = round(self._latest_bytes_count[address][traffic_type] / self._last_interval)
        #return self._convert_bytes_to_requested_unit(bytes_per_second)
        #try:
        #    bytes_per_second = round(self._latest_bytes_count[address][traffic_type] / self._last_interval)
        #    return self._convert_bytes_to_requested_unit(bytes_per_second)
        #except:
        #    return 0

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
