"""How the two machines reach each other. `cable`: direct cable, main PC shares its connection."""
from pathlib import Path

from ..steps import cmd_step, manual_step


def ssh_config(repo, v):
    """main/ssh-config rendered for this setup → (twin alias block, twin-unlock alias block)."""
    text = (Path(repo) / "main" / "ssh-config").read_text()
    text = text.replace("@TWIN_USER@", v["user"]).replace("10.42.0.11", v["addr"])
    text = text.replace("Host twin-unlock", f"Host {v['host']}-unlock").replace("Host twin\n", f"Host {v['host']}\n")
    lines = text.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("Host ") and line.rstrip().endswith("-unlock"))
    j = i - 1 if i > 0 and lines[i - 1].startswith("#") else i
    return "\n".join(lines[:j]).strip() + "\n", "\n".join(lines[j:]).strip() + "\n"


class Cable:
    def steps(self, profile, repo, v):
        twin_block, _ = ssh_config(repo, v)
        addr_re = v["addr"].replace(".", "\\.")
        con = f'"$(nmcli -g GENERAL.CONNECTION device show {v["twin_if"]})"'
        return [
            manual_step("connection.main.shared", "connection", "main",
                        "share this PC's wired connection with the twin",
                        "Settings → Network → Wired → IPv4 → 'Shared to other computers', then plug in the cable.",
                        check="ip -4 -o addr show | grep -q ' 10\\.42\\.0\\.1/24 '"),
            cmd_step("connection.main.ssh-alias", "connection", "main",
                     f"add the ssh alias '{v['host']}' to ~/.ssh/config",
                     check=f"grep -q '^Host {v['host']}$' ~/.ssh/config 2>/dev/null",
                     apply="mkdir -p ~/.ssh && touch ~/.ssh/config && chmod 600 ~/.ssh/config"
                           " && { printf '\\n'; cat; } >> ~/.ssh/config",
                     input=twin_block),
            manual_step("connection.main.key-login", "connection", "main",
                        "log in to the twin with your SSH key",
                        f"In a terminal run: ssh-copy-id {v['user']}@{v['addr']}",
                        check=f"ssh -o BatchMode=yes -o ConnectTimeout=5 {v['host']} true"),
            cmd_step("connection.twin.static-ip", "connection", "twin",
                     f"give the twin the fixed address {v['addr']}",
                     check=f"ip -4 -o addr show | grep -q ' {addr_re}/24 '"
                           f" && nmcli -g ipv4.method connection show {con} | grep -qx manual",
                     apply=f"nmcli connection modify {con} ipv4.method manual ipv4.addresses {v['addr']}/24"
                           f" ipv4.gateway 10.42.0.1 ipv4.dns '10.42.0.1 1.1.1.1' && nmcli connection up {con}",
                     root=True, check_root=False),
        ]
