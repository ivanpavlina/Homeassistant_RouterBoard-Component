"""RouterBoard client API."""
import logging
import ipaddress

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import callback
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity, async_generate_entity_id

from . import DATA_ROUTERBOARD, DATA_UPDATED, CONST_SENSOR_NETWORK, _is_address_a_network

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the RouterBoard sensors."""
    if discovery_info is None:
        return

    rb_api = hass.data[DATA_ROUTERBOARD]
    client_name = discovery_info['client_name']

    _LOGGER.info("Setting up RouterBoard sensor platform")

    if discovery_info['sensor_type'] is CONST_SENSOR_NETWORK:
        requested_addresses = discovery_info['monitored_addresses']

        expand_network_hosts = discovery_info['expand_network_hosts']
        monitored_traffic = discovery_info['monitored_traffic']

        # Filter monitored addresses, generate all valid hosts if network is supplied and expand_network_hosts is true.
        # Also monitor network as a whole
        monitored_addresses = []
        for address in requested_addresses:
            try:
                if _is_address_a_network(address):
                    _LOGGER.debug(f"Tracking requested network {address}")
                    monitored_addresses.append(address)

                    if expand_network_hosts:
                        valid_hosts = rb_api.get_all_hosts_from_network(address)
                        _LOGGER.debug(f"Adding {len(valid_hosts)} hosts sensors due to requested network {address} expansion")
                        monitored_addresses.extend(rb_api.get_all_hosts_from_network(address))
                else:
                    if rb_api.host_exists(address):
                        _LOGGER.debug(f"Requested host {address} found, tracking")
                        monitored_addresses.append(address)
                    else:
                        _LOGGER.info(f"Requested host {address} is not found in leases, will not track")
            except ValueError:
                _LOGGER.warning(f"Invalid address [{address}] specified. "
                                f"IPv4 address (192.168.1.1) or IPv4 network (192.168.1.0/24) supported only")

        _LOGGER.info(f"Generating {len(monitored_addresses)} network sensors")
        _LOGGER.debug(f">>>{monitored_addresses}")

        dev = []
        for address in monitored_addresses:
            for traffic in monitored_traffic:
                dev.append(RouterBoardAddressSensor(hass, rb_api, client_name, address, traffic))

        async_add_entities(dev, True)


class RouterBoardAddressSensor(Entity):
    """Base for a RouterBoard address sensor."""

    def __init__(self, hass, rb_api, client_name, address, sensor_type):
        """Initialize base sensor."""
        self._rb_api = rb_api
        self._client_name = client_name
        self._address = address

        self._state = None
        self._attributes = {}

        self._sensor_type = sensor_type  # Active, Download, Upload, Local, WAN(Download+Local)

        is_network = _is_address_a_network(self._address)
        name_type = {'net' if is_network else 'host'}
        name_suffix = ('active_hosts' if is_network else 'activity') if self._sensor_type == 'active' else self._sensor_type
        entity_name = f'{self._client_name}_{name_type}_{self._address}_{name_suffix}'

        self.entity_id = async_generate_entity_id(ENTITY_ID_FORMAT, entity_name, hass=hass)

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
            return f'Network {self._address} {self._sensor_type.capitalize()}'
        else:
            return f'{self._rb_api.get_address_name(self._address)}'

    @property
    def state(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def unit_of_measurement(self):
        if self._sensor_type == 'active':
            return None
        return self._rb_api.traffic_unit

    @property
    def device_state_attributes(self):
        return self._attributes

    @property
    def should_poll(self):
        """Return the polling requirement for this sensor."""
        return False

    @property
    def available(self):
        """Could the device be accessed during the last update call."""
        return self._rb_api.available

    def update(self):
        """Get the latest data from RouterBooard API and updates the state."""
        try:
            if self._sensor_type == 'active':
                if _is_address_a_network(self._address):
                    self._state = len(self._rb_api.get_active_hosts_in_network(self._address))
                else:
                    self._state = STATE_ON if self._rb_api.host_is_active(self._address) else STATE_OFF
            else:
                if _is_address_a_network(self._address):
                    self._state = self._rb_api.get_network_traffic_value(self._address, self._sensor_type) or 0
                else:
                    self._state = self._rb_api.get_address_traffic_value(self._address, self._sensor_type)
        except Exception as e:
            _LOGGER.warning(f"Exception occurred while updating sensor [{self._sensor_type}][{self._address}] - {type(e)} {e.args}")

        # Retrieve attributes for all sensors except 'active'
        if self._sensor_type == 'active':
            return

        try:
            if _is_address_a_network(self._address):
                pps = self._rb_api.get_network_packet_value(self._address, self._sensor_type) or 0
            else:
                pps = self._rb_api.get_address_packet_value(self._address, self._sensor_type) or 0
            self._attributes = {'packets_per_second': pps}
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while updating sensor attributes [{self._sensor_type}][{self._address}] - {type(e)} {e.args}")
