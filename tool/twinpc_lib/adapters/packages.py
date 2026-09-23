"""Package managers: logical package names (tool/packages.toml) → install/check commands."""
import tomllib
from pathlib import Path

from ..steps import cmd_step, unsupported_step
from .base import Unsupported

TABLE = Path(__file__).resolve().parents[2] / "packages.toml"


def table():
    with open(TABLE, "rb") as f:
        return tomllib.load(f)


class PackageAdapter:
    manager = ""

    def names(self, logical):
        t = table()
        out = []
        for name in logical:
            real = t.get(name, {}).get(self.manager)
            if not real:
                raise KeyError(name)
            out.append(real)
        return out


class Apt(PackageAdapter):
    manager = "apt"

    def check_cmd(self, names):
        return f"dpkg -s {' '.join(names)} >/dev/null 2>&1"

    def install_cmd(self, names):
        return f"DEBIAN_FRONTEND=noninteractive apt-get install -y {' '.join(names)}"


class Pacman(PackageAdapter):
    manager = "pacman"

    def check_cmd(self, names):
        return f"pacman -Q {' '.join(names)} >/dev/null 2>&1"

    def install_cmd(self, names):
        return f"pacman -S --needed --noconfirm {' '.join(names)}"


def pkg_step(feature, machine, adapter, logical, tag="packages"):
    if isinstance(adapter, Unsupported):
        return unsupported_step(feature, machine, adapter.reason(), tag)
    try:
        names = adapter.names(logical)
    except KeyError as e:
        return unsupported_step(feature, machine,
                                f"package '{e.args[0]}' is not available for {adapter.manager} yet (planned)", tag)
    return cmd_step(f"{feature}.{machine}.{tag}", feature, machine, "install " + ", ".join(names),
                    adapter.check_cmd(names), adapter.install_cmd(names), root=True, check_root=False)
