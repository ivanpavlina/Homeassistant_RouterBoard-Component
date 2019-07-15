"""Support for monitoring the RouterBoardclient API."""
from datetime import timedelta
import logging

from homeassistant.core import callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity
import ipaddress
from . import DATA_ROUTERBOARD, DATA_UPDATED

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


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the RouterBoard sensors."""
    if discovery_info is None:
        return

    rb_api = hass.data[DATA_ROUTERBOARD]
    conditions = discovery_info['sensors']
    client_name = discovery_info['client_name']
    expand_network_hosts = discovery_info['expand_network']

    # Filter monitored conditions, generate all valid addresses if network is supplied. Also monitor network
    monitored_addresses = []

    for condition in conditions:
        try:
            if _is_address_a_network(condition):
                _LOGGER.debug(f"Tracking requested network {condition}")
                monitored_addresses.append(condition)

                if expand_network_hosts:
                    valid_hosts = rb_api.get_all_hosts_from_network(condition)
                    _LOGGER.info(f"Adding {len(valid_hosts)} hosts sensors due to requested network {condition} expansion")
                    monitored_addresses.extend(rb_api.get_all_hosts_from_network(condition))
            else:
                if rb_api.host_exists(condition):
                    _LOGGER.debug(f"Requested host {condition} found, tracking")
                    monitored_addresses.append(condition)
                else:
                    _LOGGER.info(f"Requested host {condition} is not found in leases, will not track")
        except ValueError:
            _LOGGER.warning(f"Invalid address [{condition}] specified. "
                            f"IPv4 address (192.168.1.1) or IPv4 network (192.168.1.0/24) supported only")

    _LOGGER.info(f"Monitoring {len(monitored_addresses)} addresses")
    _LOGGER.debug(f">>>{monitored_addresses}")

    dev = []
    for host in monitored_addresses:
        dev.append(RouterBoardAddressSensor(rb_api, client_name, host))

    async_add_entities(dev, True)


class RouterBoardAddressSensor(Entity):
    """Base for a RouterBoard address sensor."""

    def __init__(self, rb_api, client_name, address):
        """Initialize base sensor."""
        self._rb_api = rb_api
        self._client_name = client_name
        self._address = address
        self._state = None

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update)

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)

    @property
    def name(self):
        """Return the name of the sensor."""
        if _is_address_a_network(self._address):
            return f'{self._client_name}_network_{self._address}'
        else:
            return f'{self._client_name}_host_{self._address}'

    @property
    def device_state_attributes(self):
        if _is_address_a_network(self._address):
            response = {'name': f'{self._address} network',
                        'download_traffic': self._rb_api.get_network_traffic_value(self._address, 'download'),
                        'download_packets': self._rb_api.get_network_packet_value(self._address, 'download'),
                        'upload_traffic': self._rb_api.get_network_traffic_value(self._address, 'upload'),
                        'upload_packets': self._rb_api.get_network_packet_value(self._address, 'upload'),

                        'local_traffic_with_unit': f"{self._rb_api.get_network_traffic_value(self._address, 'local')} {self._rb_api.traffic_unit}",
                        'local_packets_with_unit': f"{self._rb_api.get_network_packet_value(self._address, 'local')} P/s",
                        'download_traffic_with_unit': f"{self._rb_api.get_network_traffic_value(self._address, 'download')} {self._rb_api.traffic_unit}",
                        'download_packets_with_unit': f"{self._rb_api.get_network_packet_value(self._address, 'download')} P/s",
                        'upload_traffic_with_unit': f"{self._rb_api.get_network_traffic_value(self._address, 'upload')} {self._rb_api.traffic_unit}",
                        'upload_packets_with_unit': f"{self._rb_api.get_network_packet_value(self._address, 'upload')} P/s"}
        else:
            response = {'mac_address': self._rb_api.get_address_mac(self._address),
                        'pretty_name': self._rb_api.get_address_name(self._address),
                        'active': self._rb_api.get_address_active_state(self._address),
                        'local_traffic': self._rb_api.get_address_traffic_value(self._address, 'local'),
                        'local_packets': self._rb_api.get_address_packet_value(self._address, 'local'),
                        'download_traffic': self._rb_api.get_address_traffic_value(self._address, 'download'),
                        'download_packets': self._rb_api.get_address_packet_value(self._address, 'download'),
                        'upload_traffic': self._rb_api.get_address_traffic_value(self._address, 'upload'),
                        'upload_packets': self._rb_api.get_address_packet_value(self._address, 'upload'),

                        'local_traffic_with_unit': f"{self._rb_api.get_address_traffic_value(self._address, 'local')} {self._rb_api.traffic_unit}",
                        'local_packets_with_unit': f"{self._rb_api.get_address_packet_value(self._address, 'local')} P/s",
                        'download_traffic_with_unit': f"{self._rb_api.get_address_traffic_value(self._address, 'download')} {self._rb_api.traffic_unit}",
                        'download_packets_with_unit': f"{self._rb_api.get_address_packet_value(self._address, 'download')} P/s",
                        'upload_traffic_with_unit': f"{self._rb_api.get_address_traffic_value(self._address, 'upload')} {self._rb_api.traffic_unit}",
                        'upload_packets_with_unit': f"{self._rb_api.get_address_packet_value(self._address, 'upload')} P/s"}

        return response

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def should_poll(self):
        """Return the polling requirement for this sensor."""
        return False

    @property
    def unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return self._rb_api.traffic_unit

    @property
    def available(self):
        """Could the device be accessed during the last update call."""
        return self._rb_api.available

    def update(self):
        """Get the latest data from RouterBooard API and updates the state."""
        if _is_address_a_network(self._address):
            self._state = self._rb_api.get_network_traffic_value(self._address, 'download')
        else:
            self._state = self._rb_api.get_address_traffic_value(self._address, 'download')
