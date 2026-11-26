"""Application settings. Standard library only, so nothing extra to install."""
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent       # the project root


@dataclass(frozen=True)
class Settings:
    client_pack_path: Path
    enable_dev_endpoints: bool = False      # DEV_ENDPOINTS=1 turns on /dev/* for manual testing


def get_settings() -> Settings:
    """Read settings from environment variables, with safe defaults."""
    raw_path = os.getenv("CLIENT_PACK_PATH", "client_packs/demo/decision.yaml")
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path                           # relative to the project root
    dev = os.getenv("DEV_ENDPOINTS", "").lower() in ("1", "true", "yes")
    return Settings(client_pack_path=path, enable_dev_endpoints=dev)