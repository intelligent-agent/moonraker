# Machine manipulation request handlers
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
# Copyright (C) 2022 Elias Bakken <elias@iagent.no>
#
# This file may be distributed under the terms of the GNU GPLv3 license.

from __future__ import annotations
import sys
import logging
import asyncio
import time
import getpass

# Annotation imports
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Union
)

if TYPE_CHECKING:
    from confighelper import ConfigHelper
    from websockets import WebRequest
    from .shell_command import ShellCommandFactory as SCMDComp
    from .authorization import Authorization
    from .announcements import Announcements
    SudoReturn = Union[Awaitable[Tuple[str, bool]], Tuple[str, bool]]
    SudoCallback = Callable[[], SudoReturn]

class Recore:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.inside_container = False

        pclass = BaseProvider
        self.sys_provider: BaseProvider = pclass(config)

        self.server.register_endpoint(
            "/recore/enable_ssh", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/recore/set_boot_media", ['POST'], self._handle_machine_request)
        self.server.register_endpoint(
            "/recore/state", ['GET'], self._handle_recore_state_request)
        # Register remote methods
        self.server.register_remote_method(
            "enable_ssh", self.sys_provider.enable_ssh)
        self.server.register_remote_method(
            "set_boot_media", self.sys_provider.set_boot_media)

        shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')
        get_boot_media_bin = "/usr/local/bin/get-boot-media"
        self.get_boot_media_cmd = shell_cmd.build_shell_command(
            get_boot_media_bin)
        is_ssh_enabled_bin = "/usr/local/bin/is-ssh-enabled"
        self.is_ssh_enabled_cmd = shell_cmd.build_shell_command(
            is_ssh_enabled_bin)
        self.init_evt = asyncio.Event()

    def get_system_provider(self):
        return self.sys_provider

    def is_inside_container(self):
        return self.inside_container

    def get_provider_type(self):
        return self.provider_type

    async def wait_for_init(
        self, timeout: Optional[float] = None
    ) -> None:
        try:
            await asyncio.wait_for(self.init_evt.wait(), timeout)
        except asyncio.TimeoutError:
            pass

    async def component_init(self) -> None:
        await self.sys_provider.initialize()
        self.init_evt.set()

    async def _handle_machine_request(self, web_request: WebRequest) -> str:
        ep = web_request.get_endpoint()
        if self.inside_container:
            virt_id = self.system_info['virtualization'].get(
                'virt_identifier', "none")
            raise self.server.error(
                f"Cannot {ep.split('/')[-1]} from within a "
                f"{virt_id} container")
        if ep == "/recore/enable_ssh":
            logging.info("/recore/enable_ssh")
            await self.sys_provider.enable_ssh("true")
        elif ep == "/recore/set_boot_media":
            logging.info("/recore/set_boot_media")
            await self.sys_provider.set_boot_media("usb")
        else:
            raise self.server.error("Unsupported machine request")
        return "ok"

    async def _handle_recore_state_request(self,
                                      web_request: WebRequest
                                      ) -> Dict[str, Any]:
        boot_media = await self._get_boot_media()
        ssh_enabled = await self._get_ssh_enabled()
        recore_state = {
            "ssh_enabled": ssh_enabled,
            "boot_media": boot_media
        }
        return {"recore_state": recore_state}

    @property
    def sudo_password(self) -> Optional[str]:
        return self._sudo_password

    @sudo_password.setter
    def sudo_password(self, pwd: Optional[str]) -> None:
        self._sudo_password = pwd

    @property
    def sudo_requested(self) -> bool:
        return len(self.sudo_requests) > 0

    @property
    def linux_user(self) -> str:
        return getpass.getuser()

    @property
    def sudo_request_messages(self) -> List[str]:
        return [req[1] for req in self.sudo_requests]

    def register_sudo_request(
        self, callback: SudoCallback, message: str
    ) -> None:
        self.sudo_requests.append((callback, message))
        self.server.send_event(
            "machine:sudo_alert",
            {
                "sudo_requested": True,
                "request_messages": self.sudo_request_messages
            }
        )

    async def check_sudo_access(self, cmds: List[str] = []) -> bool:
        if not cmds:
            cmds = ["systemctl --version", "ls /root"]
        shell_cmd: SCMDComp = self.server.lookup_component("shell_command")
        for cmd in cmds:
            try:
                await self.exec_sudo_command(cmd, timeout=10.)
            except shell_cmd.error:
                return False
        return True

    async def exec_sudo_command(
        self, command: str, tries: int = 1, timeout=2.
    ) -> str:
        proc_input = None
        full_cmd = f"sudo {command}"
        if self._sudo_password is not None:
            proc_input = self._sudo_password
            full_cmd = f"sudo -S {command}"
        shell_cmd: SCMDComp = self.server.lookup_component("shell_command")
        return await shell_cmd.exec_cmd(
            full_cmd, proc_input=proc_input, log_complete=False, retries=tries,
            timeout=timeout
        )

    async def _get_boot_media(self) -> str:
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        try:
            resp = await self.get_boot_media_cmd.run_with_response(log_complete=False)
        except shell_cmd.error:
            logging.info("Failed to run 'get-boot-media' command")
            return "Failed"
        if resp:
            return resp.strip()
        return "unknown"

    async def _get_ssh_enabled(self) -> str:
        shell_cmd: SCMDComp = self.server.lookup_component('shell_command')
        try:
            resp = await self.is_ssh_enabled_cmd.run_with_response(log_complete=False)
        except shell_cmd.error:
            logging.info("Failed to run 'get-ssh-enabled' command")
            return "Failed"
        if resp:
            return resp.strip()
        return "unknown"


class BaseProvider:
    def __init__(self, config: ConfigHelper) -> None:
        self.server = config.get_server()
        self.available_services: Dict[str, Dict[str, str]] = {}
        self.shell_cmd: SCMDComp = self.server.load_component(
            config, 'shell_command')

    async def initialize(self) -> None:
        pass

    async def check_virt_status(self) -> Dict[str, Any]:
        return {
            'virt_type': "unknown",
            'virt_identifier': "unknown"
        }

    async def _exec_sudo_command(self, command: str):
        machine: Machine = self.server.lookup_component("machine")
        return await machine.exec_sudo_command(command)

    async def enable_ssh(self, enabled) -> None:
        await self._exec_sudo_command(f"/usr/local/bin/set-ssh-access {enabled}")
        logging.info("Enable ssh OK")

    async def set_boot_media(self, media) -> None:
        await self._exec_sudo_command(f"/usr/local/bin/set-boot-media {media}")
        logging.info("Set boot media OK")

def load_component(config: ConfigHelper) -> Recore:
    return Recore(config)
