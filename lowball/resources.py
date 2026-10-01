import os
import sys


def get_asset_path(filename: str) -> str:
    """Resolve an asset path from source or a PyInstaller bundle."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return os.path.join(bundle_dir, "lowball", "assets", filename)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", filename)
