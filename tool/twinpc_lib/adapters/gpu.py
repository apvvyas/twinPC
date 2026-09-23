"""GPU vendors: the AI stack on the twin (Ollama, Docker, PyTorch)."""
import re
from pathlib import Path

from ..steps import cmd_step, unit_step
from .packages import pkg_step


def hsa_override(gfx):
    """ROCm only ships kernels for some consumer GPUs; close relatives run with an override."""
    m = re.fullmatch(r"gfx(\d+)(\d)([0-9a-f])", gfx or "")
    if not m:
        return ""
    major, minor, step = m.group(1), m.group(2), m.group(3)
    if major == "10" and minor == "3" and step != "0":
        return "10.3.0"                     # RDNA2 consumer cards (RX 6000 series)
    if major == "11" and minor == "0" and step != "0":
        return "11.0.0"                     # RDNA3 cards other than gfx1100
    return ""


class Amd:
    def ai_stack_steps(self, profile, repo, v, pk):
        hsa = hsa_override(profile.get("twin", {}).get("gpu_arch", ""))
        override = ("[Service]\nEnvironment=\"OLLAMA_HOST=0.0.0.0:11434\"\n"
                    + (f'Environment="HSA_OVERRIDE_GFX_VERSION={hsa}"\n' if hsa else "")
                    + 'Environment="OLLAMA_KEEP_ALIVE=30m"\n')
        return [
            pkg_step("gpu-stack", "twin", pk, ["ollama-rocm", "docker"]),
            cmd_step("gpu-stack.twin.ollama-config", "gpu-stack", "twin",
                     "let Ollama serve the main PC and use the GPU",
                     check="grep -q 'OLLAMA_HOST=0.0.0.0' /etc/systemd/system/ollama.service.d/override.conf 2>/dev/null",
                     apply="mkdir -p /etc/systemd/system/ollama.service.d"
                           " && cat > /etc/systemd/system/ollama.service.d/override.conf && systemctl daemon-reload",
                     root=True, check_root=False, input=override),
            unit_step("gpu-stack.twin.ollama", "gpu-stack", "twin", "ollama", user=False),
            unit_step("gpu-stack.twin.docker", "gpu-stack", "twin", "docker", user=False),
            cmd_step("gpu-stack.twin.groups", "gpu-stack", "twin", f"add {v['user']} to docker, render and video",
                     check=f"[ \"$(id -nG {v['user']} | tr ' ' '\\n' | grep -cxE 'docker|render|video')\" = 3 ]",
                     apply=f"usermod -aG docker,render,video {v['user']}", root=True, check_root=False),
            cmd_step("gpu-stack.twin.firewall", "gpu-stack", "twin", "allow Ollama (11434/tcp) in the twin's firewall",
                     check="! command -v ufw >/dev/null || ufw status | grep -q '^11434/tcp'",
                     apply="ufw allow 11434/tcp", root=True),
            cmd_step("gpu-stack.twin.pytorch", "gpu-stack", "twin", "install PyTorch for ROCm in ~/ai-env (~4 GB)",
                     check='~/ai-env/bin/python -c "import torch" 2>/dev/null',
                     apply="bash -s", input=(Path(repo) / "twinpc" / "setup-ai-env.sh").read_text()),
        ]
