from __future__ import annotations

from typing import Optional, Literal, Any, overload

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from tuya_sharing import (
    CustomerDevice,
)
from tuya_sharing.home import (
    HomeRepository,
)
from tuya_sharing.scenes import (
    SceneRepository,
)
from tuya_sharing.user import (
    UserRepository,
)
from tuya_sharing.customerapi import (
    CustomerTokenInfo,
    CustomerApi,
)

from .xt_tuya_sharing_manager import (
    XTSharingDeviceManager,
)
from ..shared.interface.device_manager import (
    XTDeviceManagerInterface,
)
from ..shared.shared_classes import (
    XTConfigEntry,
)
from ..shared.device import (
    XTDevice,
)
from .xt_tuya_sharing_data import (
    TuyaSharingData,
)
from .xt_tuya_sharing_token_listener import (
    XTSharingTokenListener,
)
from .xt_tuya_sharing_device_repository import (
    XTSharingDeviceRepository,
)
from .ha_tuya_integration.config_entry_handler import (
    XTHATuyaIntegrationConfigEntryManager
)
from .ha_tuya_integration.tuya_decorators import (
    decorate_tuya_manager,
    decorate_tuya_integration,
)
from .const import (
    CONF_TERMINAL_ID,
    CONF_TOKEN_INFO,
    CONF_USER_CODE,
    CONF_ENDPOINT,
    TUYA_CLIENT_ID,
    DOMAIN_ORIG,
)
from .util import (
    get_overriden_tuya_integration_runtime_data,
)
from .ha_tuya_integration.platform_descriptors import (
    get_tuya_platform_descriptors
)
from ..multi_manager import (
    MultiManager,
)
from ...const import (
    DOMAIN,
    MESSAGE_SOURCE_TUYA_SHARING,
    MESSAGE_SOURCE_TUYA_IOT,
    TUYA_DISCOVERY_NEW,
    TUYA_DISCOVERY_NEW_ORIG,
    TUYA_HA_SIGNAL_UPDATE_ENTITY,
    TUYA_HA_SIGNAL_UPDATE_ENTITY_ORIG,
)

def get_plugin_instance() -> XTTuyaSharingDeviceManagerInterface | None:
    return XTTuyaSharingDeviceManagerInterface()

class XTTuyaSharingDeviceManagerInterface(XTDeviceManagerInterface):
    def __init__(self) -> None:
        super().__init__()
        self.sharing_account: TuyaSharingData = None,
        self.hass: HomeAssistant = None

    def get_type_name(self) -> str:
        return MESSAGE_SOURCE_TUYA_SHARING

    async def setup_from_entry(self, hass: HomeAssistant, config_entry: XTConfigEntry, multi_manager: MultiManager) -> bool:
        self.multi_manager: MultiManager = multi_manager
        self.hass = hass
        self.sharing_account: TuyaSharingData = await self._init_from_entry(hass, config_entry)
        if self.sharing_account:
            return True
        return False
    
    async def _init_from_entry(self, hass: HomeAssistant, config_entry: XTConfigEntry) -> TuyaSharingData | None:
        ha_tuya_integration_config_manager: XTHATuyaIntegrationConfigEntryManager = None
        #See if our current entry is an override of a Tuya integration entry
        tuya_integration_runtime_data = get_overriden_tuya_integration_runtime_data(hass, config_entry)
        reuse_config = False
        if tuya_integration_runtime_data:
            #We are using an override of the Tuya integration
            sharing_device_manager = XTSharingDeviceManager(multi_manager=self.multi_manager, other_device_manager=tuya_integration_runtime_data.device_manager)
            ha_tuya_integration_config_manager = XTHATuyaIntegrationConfigEntryManager(sharing_device_manager, config_entry)
            decorate_tuya_manager(tuya_integration_runtime_data.device_manager, ha_tuya_integration_config_manager)
            sharing_device_manager.terminal_id      = tuya_integration_runtime_data.device_manager.terminal_id
            sharing_device_manager.mq               = tuya_integration_runtime_data.device_manager.mq
            sharing_device_manager.customer_api     = tuya_integration_runtime_data.device_manager.customer_api
            tuya_integration_runtime_data.device_manager.device_listeners.clear()
            #self.convert_tuya_devices_to_xt(tuya_integration_runtime_data.device_manager)
            reuse_config = True
        else:
            #We are using XT as a standalone integration
            sharing_device_manager = XTSharingDeviceManager(multi_manager=self.multi_manager, other_device_manager=None)
            token_listener = XTSharingTokenListener(hass, config_entry)
            sharing_device_manager.terminal_id = config_entry.data[CONF_TERMINAL_ID]
            sharing_device_manager.customer_api = CustomerApi(
                CustomerTokenInfo(config_entry.data[CONF_TOKEN_INFO]),
                TUYA_CLIENT_ID,
                config_entry.data[CONF_USER_CODE],
                config_entry.data[CONF_ENDPOINT],
                token_listener,
            )
            sharing_device_manager.mq = None
        sharing_device_manager.reuse_config = reuse_config
        sharing_device_manager.home_repository = HomeRepository(sharing_device_manager.customer_api)
        sharing_device_manager.device_repository = XTSharingDeviceRepository(sharing_device_manager.customer_api, sharing_device_manager, self.multi_manager)
        sharing_device_manager.scene_repository = SceneRepository(sharing_device_manager.customer_api)
        sharing_device_manager.user_repository = UserRepository(sharing_device_manager.customer_api)
        sharing_device_manager.add_device_listener(self.multi_manager.multi_device_listener)
        return TuyaSharingData(
            device_manager=sharing_device_manager, 
            device_ids=[], 
            ha_tuya_integration_config_manager=ha_tuya_integration_config_manager, 
            )

    def update_device_cache(self):
        try:
            self.sharing_account.device_manager.update_device_cache()
            new_device_ids: list[str] = [device_id for device_id in self.sharing_account.device_manager.device_map]
            self.sharing_account.device_ids.clear()
            self.sharing_account.device_ids.extend(new_device_ids)
        except Exception as exc:
            # While in general, we should avoid catching broad exceptions,
            # we have no other way of detecting this case.
            if "sign invalid" in str(exc):
                msg = "Authentication failed. Please re-authenticate the Tuya integration"
                if self.sharing_account.device_manager.reuse_config:
                    raise ConfigEntryNotReady(msg) from exc
                else:
                    raise ConfigEntryAuthFailed("Authentication failed. Please re-authenticate.")
            raise
    
    def get_available_device_maps(self) -> list[dict[str, XTDevice]]:
        return_list: list[dict[str, XTDevice]] = []
        """if other_manager := self.sharing_account.device_manager.get_overriden_device_manager():
            return_list.append(other_manager.device_map)"""
        return_list.append(self.sharing_account.device_manager.device_map)
        return return_list
    
    def refresh_mq(self):
        self.sharing_account.device_manager.refresh_mq()
    
    def remove_device_listeners(self) -> None:
        self.sharing_account.device_manager.remove_device_listener(self.multi_manager.multi_device_listener)
    
    def unload(self):
        if not self.multi_manager.get_account_by_name(MESSAGE_SOURCE_TUYA_IOT):
            self.sharing_account.device_manager.user_repository.unload(self.sharing_account.device_manager.terminal_id)
    
    def on_message(self, msg: str):
        self.sharing_account.device_manager.on_message(msg)
    
    def query_scenes(self) -> list:
        if not self.sharing_account.device_manager.reuse_config:
            return self.sharing_account.device_manager.query_scenes()
        return []
    
    def get_device_stream_allocate(
            self, device_id: str, stream_type: Literal["flv", "hls", "rtmp", "rtsp"]
    ) -> Optional[str]:
        if device_id in self.sharing_account.device_ids:
            return self.sharing_account.device_manager.get_device_stream_allocate(device_id, stream_type)
    
    def get_device_registry_identifiers(self) -> list:
        if self.sharing_account.device_manager.reuse_config:
            return [DOMAIN_ORIG, DOMAIN]
        return [DOMAIN]
    
    def get_domain_identifiers_of_device(self, device_id: str) -> list:
        device_registry = dr.async_get(self.hass)
        if (
            self.sharing_account.device_manager.reuse_config
            and device_registry.async_get_device(identifiers={(DOMAIN_ORIG, device_id)}, connections=None)
        ):
            return [DOMAIN_ORIG, DOMAIN]
        else:
            return [DOMAIN]

    def on_update_device(self, device: XTDevice) -> list[str] | None:
        return_list: list[str] = []
        if device.id in self.sharing_account.device_ids:
            return_list.append(TUYA_HA_SIGNAL_UPDATE_ENTITY)
        if self.sharing_account.device_manager.reuse_config:
            self.sharing_account.device_manager.copy_statuses_to_tuya(device)
            return_list.append(TUYA_HA_SIGNAL_UPDATE_ENTITY_ORIG)
        if return_list:
            return return_list
        return None
    
    def on_add_device(self, device: XTDevice) -> list[str] | None:
        return_list: list[str] = []
        if device.id in self.sharing_account.device_ids:
            return_list.append(TUYA_DISCOVERY_NEW)
        if self.sharing_account.device_manager.reuse_config:
            return_list.append(TUYA_DISCOVERY_NEW_ORIG)
        if return_list:
            return return_list
        return None
    
    def on_mqtt_stop(self):
        if (
            self.sharing_account.device_manager.mq 
            and not self.sharing_account.device_manager.reuse_config
        ):
            self.sharing_account.device_manager.mq.stop()
    
    def on_post_setup(self):
        if self.sharing_account.ha_tuya_integration_config_manager:
            decorate_tuya_integration(self.sharing_account.ha_tuya_integration_config_manager)
    
    def get_platform_descriptors_to_merge(self, platform: Platform) -> Any:
        if self.sharing_account.device_manager.reuse_config:
            return None
        return get_tuya_platform_descriptors(platform)
    
    def send_commands(self, device_id: str, commands: list[dict[str, Any]]):
        regular_commands: list[dict[str, Any]] = []
        devices = self.get_devices_from_device_id(device_id)
        for command in commands:
            command_code  = command["code"]
            """command_value = command["value"]"""

            #Filter commands that require the use of OpenAPI
            skip_command = False
            for device in devices:
                if dpId := self.multi_manager._read_dpId_from_code(command_code, device):
                    if device.local_strategy[dpId].get("use_open_api", False):
                        skip_command = True
                        break
            if not skip_command:
                regular_commands.append(command)
        
        if regular_commands:
            self.sharing_account.device_manager.send_commands(device_id, regular_commands)
    

    @overload
    def convert_to_xt_device(self, Any) -> XTDevice: ...
    
    def convert_to_xt_device(self, device: CustomerDevice) -> XTDevice:
        return XTDevice.from_compatible_device(device)