"""Platform adapters. get(kind, value) returns the adapter for a detected value, or Unsupported."""
from . import bootunlock, desktop, gpu, network, packages
from .base import Unsupported

PLANNED = {
    "packages": {"dnf"},
    "desktop": {"kde", "sway", "x11-other"},
    "gpu": {"nvidia", "intel", "none"},
    "bootunlock": {"dracut", "initramfs-tools"},
    "network": {"lan"},
}

REGISTRY = {
    ("packages", "apt"): packages.Apt(),
    ("packages", "pacman"): packages.Pacman(),
    ("desktop", "gnome"): desktop.Gnome(),
    ("desktop", "hyprland"): desktop.Hyprland(),
    ("gpu", "amd"): gpu.Amd(),
    ("bootunlock", "mkinitcpio"): bootunlock.Mkinitcpio(),
    ("network", "cable"): network.Cable(),
}


def get(kind, value):
    adapter = REGISTRY.get((kind, value))
    if adapter is not None:
        return adapter
    return Unsupported(kind, value or "unknown", value in PLANNED.get(kind, set()))
