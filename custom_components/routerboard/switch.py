"""RouterBoard client API."""
import logging

from homeassistant.core import callback
from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchDevice
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.const import STATE_UNKNOWN, STATE_ON, STATE_OFF

from . import DATA_ROUTERBOARD, DATA_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the RouterBoard switches."""
    if discovery_info is None:
        return

    _LOGGER.info("Setting up RouterBoard switch platform")

    rb_api = hass.data[DATA_ROUTERBOARD]
    client_name = discovery_info['client_name']

    switches = []

    if discovery_info['manage_queues']:
        monitored_queues = rb_api.get_queue_list()
        _LOGGER.info(f"Generating {len(monitored_queues)} queue switches")
        for queue_id in monitored_queues:
            switches.append(RouterBoardQueueSwitch(hass, rb_api, client_name, queue_id))

    if discovery_info['custom_switches']:
        for switch in discovery_info['custom_switches']:
            try:
                switch_name = switch['name']

            except KeyError:
                _LOGGER.warning("Missing 'name' in custom switch configuration!")
                continue

            try:
                _LOGGER.info(f"Switch turn on action: {switch['turn_on']['cmd']}")
                _LOGGER.info(f"Switch turn off action: {switch['turn_off']['cmd']}")
                _LOGGER.info(f"Switch state action: {switch['state']['cmd']}")
            except KeyError:
                _LOGGER.warning(f"Invalid config for {switch_name}!")
                continue

            # Wont check for switch['state']['args'], these are optional

            _LOGGER.info(f"Generating custom switch [{switch_name}]")
            switches.append(RouterBoardCustomSwitch(hass, rb_api, client_name, switch))

    async_add_entities(switches, True)


class RouterBoardQueueSwitch(SwitchDevice):
    """Base for a RouterBoard Queue Switch."""

    def __init__(self, hass, rb_api, client_name, queue_id):
        """Initialize switch."""
        self._rb_api = rb_api
        self._client_name = client_name
        self._queue_id = queue_id

        self._name = None
        self._state = None
        self._attributes = {}

        entity_name = f'{self._client_name}_queue_{self._rb_api.get_queue_target(self._queue_id)}_{self._queue_id}'

        self.entity_id = async_generate_entity_id(ENTITY_ID_FORMAT, entity_name, hass=hass)

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update)

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)

    @property
    def is_on(self) -> bool:
        return self._state

    def turn_on(self, **kwargs) -> None:
        try:
            self._rb_api.set_queue_state(self._queue_id, True)
            self._schedule_immediate_update()
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while turning queue on [{self._queue_id}] - {type(e)} {e.args}")

    def turn_off(self, **kwargs) -> None:
        try:
            self._rb_api.set_queue_state(self._queue_id, False)
            self._schedule_immediate_update()
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while turning queue off [{self._queue_id}] - {type(e)} {e.args}")

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    @property
    def device_state_attributes(self):
        return self._attributes

    def update(self):
        """Get the latest data from RouterBoard API and updates the state."""
        try:
            self._state = self._rb_api.get_queue_state(self._queue_id)
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while retrieving updating queue state [{self._queue_id}] - {type(e)} {e.args}")

        try:
            limits = self._rb_api.get_queue_limits(self._queue_id)
            self._attributes = {'target': ", ".join(self._rb_api.get_queue_target(self._queue_id).split(",")),
                                'download-limit': limits[1],
                                'upload-limit': limits[0]}
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while retrieving updating queue attributes [{self._queue_id}] - {type(e)} {e.args}")

        try:
            self._name = f'{self._rb_api.get_queue_name(self._queue_id)}'
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while retrieving updating queue name [{self._queue_id}] - {type(e)} {e.args}")


class RouterBoardCustomSwitch(SwitchDevice):
    """Base for a RouterBoard Custom Switch."""

    def __init__(self, hass, rb_data, client_name, switch_data):
        """Initialize switch."""
        self._rb_data = rb_data
        self._client_name = client_name
        self._config = switch_data

        self._name = f"{self._config['name']}"
        self._state = None

        entity_name = f"{self._client_name}_switch_{self._config.get('name')}"

        self.entity_id = async_generate_entity_id(ENTITY_ID_FORMAT, entity_name, hass=hass)

    async def async_added_to_hass(self):
        """Handle entity which will be added."""
        async_dispatcher_connect(
            self.hass, DATA_UPDATED, self._schedule_immediate_update)

    @callback
    def _schedule_immediate_update(self):
        self.async_schedule_update_ha_state(True)

    @property
    def is_on(self) -> bool:
        return self._state

    def turn_on(self, **kwargs) -> None:
        try:
            self._rb_data.run_raw_command(self._config['turn_on']['cmd'], self._config['turn_on'].get('args'))
            self._schedule_immediate_update()
        except Exception as e:
            _LOGGER.warning(f"Could not turn on custom switch {self._config['name']} >> {type(e)}  {e.args}")

    def turn_off(self, **kwargs) -> None:
        try:
            self._rb_data.run_raw_command(self._config['turn_off']['cmd'], self._config['turn_off'].get('args'))
            self._schedule_immediate_update()
        except Exception as e:
            _LOGGER.warning(f"Could not turn off custom switch {self._config['name']} >> {type(e)}  {e.args}")

    @property
    def name(self):
        """Return the name of the switch."""
        return self._name

    def update(self):
        """Get the latest data from RouterBoard API and updates the state."""
        try:
            response = self._rb_data.run_raw_command(self._config['state']['cmd'], self._config['state'].get('args'))
            state = STATE_UNKNOWN
            for item in response:
                if item.get('invalid') is False and item.get('disabled') is False:
                    state = STATE_ON
                else:
                    state = STATE_OFF

            self._state = state
        except Exception as e:
            _LOGGER.warning(f"Could not update custom switch {self._config['name']} >> {type(e)}  {e.args}")
            self._state = STATE_UNKNOWN

