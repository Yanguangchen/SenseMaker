"""Start the pinned Streamlit runtime with login assets ready on every replica.

Railway can send component HTTP requests to a fresh container that has never run
the dashboard script. Register public sign-in assets before accepting requests,
instead of waiting for a user's WebSocket session to declare the component.
"""

from __future__ import annotations

import os
from pathlib import Path

from streamlit.components.v1.custom_component import CustomComponent
from streamlit.runtime import get_instance
from streamlit.web import bootstrap
from streamlit.web.server import Server

from modules.component_assets import sign_in_component_path

APP_DIR = Path(__file__).resolve().parent


class HostedServer(Server):
    def __init__(self, main_script_path: str, is_hello: bool):
        super().__init__(main_script_path, is_hello)
        get_instance().component_registry.register_component(CustomComponent(
            name="modules.auth.sensemaker_google_sign_in",
            path=sign_in_component_path(), module_name="modules.auth",
        ))


def main() -> None:
    # Server/registry APIs are verified against requirements-hosted.txt's pin.
    bootstrap.Server = HostedServer
    options = {
        "global.developmentMode": False,
        "server.address": "0.0.0.0", "server.port": int(os.getenv("PORT", "8080")),
        "server.headless": True, "server.fileWatcherType": "none",
        "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(options)
    bootstrap.run(str(APP_DIR / "dashboard.py"), False, [], options)


if __name__ == "__main__":
    main()
