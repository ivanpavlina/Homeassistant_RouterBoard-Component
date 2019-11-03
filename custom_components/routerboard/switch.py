"""RouterBoard client API."""
import logging

from homeassistant.core import callback
from homeassistant.components.switch import ENTITY_ID_FORMAT, SwitchDevice
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import async_generate_entity_id

from . import DATA_ROUTERBOARD, DATA_UPDATED

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the RouterBoard Queues switches."""

    if discovery_info is None:
        return

    _LOGGER.info("Setting up Routerboard switch platform")

    rb_api = hass.data[DATA_ROUTERBOARD]
    client_name = discovery_info['client_name']

    dev = []
    monitored_queues = rb_api.get_queue_list()
    _LOGGER.info(f"Generating {len(monitored_queues)} queue switches")

    for queue_id in monitored_queues:
        dev.append(RouterBoardQueueSwitch(hass, rb_api, client_name, queue_id))

    async_add_entities(dev, True)


class RouterBoardQueueSwitch(SwitchDevice):
    """Base for a RouterBoard Queue Switch."""

    def __init__(self, hass, rb_api, client_name, queue_id):
        """Initialize switch."""
        self._rb_api = rb_api
        self._client_name = client_name
        self._queue_id = queue_id

        self._state = None

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
        self._rb_api.set_queue_state(self._queue_id, True)
        self._schedule_immediate_update()

    def turn_off(self, **kwargs) -> None:
        self._rb_api.set_queue_state(self._queue_id, False)
        self._schedule_immediate_update()

    @property
    def name(self):
        """Return the name of the switch."""
        #return f'{self._rb_api.get_queue_name(self._queue_id).split("@")[0]} Queue'
        return f'{self._rb_api.get_queue_name(self._queue_id)}'

    @property
    def device_state_attributes(self):
        limits = self._rb_api.get_queue_limits(self._queue_id)
        return {'target': ", ".join(self._rb_api.get_queue_target(self._queue_id).split(",")),
                'download-limit': limits[1],
                'upload-limit': limits[0]}

    def update(self):
        """Get the latest data from RouterBoard API and updates the state."""
        try:
            self._state = self._rb_api.get_queue_state(self._queue_id)
        except Exception as e:
            _LOGGER.warning(
                f"Exception occurred while retrieving updating queue state [{self._queue_id}] - {type(e)} {e.args}")
