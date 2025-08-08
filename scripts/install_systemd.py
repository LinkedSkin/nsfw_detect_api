import os
from pathlib import Path
from textwrap import dedent
import shutil

SERVICE_NAME = "nsfw_detect_api"

def main():
    cwd = Path.cwd()
    pdm_path = shutil.which("pdm")

    if not pdm_path:
        raise FileNotFoundError("Could not find 'pdm' in PATH. Is it installed globally?")

    unit_file = dedent(f"""
        [Unit]
        Description=NSFW Detect API
        After=network.target

        [Service]
        Type=simple
        User=pi
        WorkingDirectory={cwd}
        ExecStart={pdm_path} run run
        Restart=always
        RestartSec=5
        Environment=PYTHONUNBUFFERED=1

        [Install]
        WantedBy=multi-user.target
    """)

    # Write the service file locally
    temp_file = Path(f"{SERVICE_NAME}.service")
    with temp_file.open("w") as f:
        f.write(unit_file.strip() + "\n")

    print(f"Installing systemd service from {cwd}...")

    os.system(f"sudo mv {temp_file} /etc/systemd/system/{SERVICE_NAME}.service")
    os.system("sudo systemctl daemon-reexec")
    os.system(f"sudo systemctl enable {SERVICE_NAME}")
    os.system(f"sudo systemctl restart {SERVICE_NAME}")
    print(f"{SERVICE_NAME} is now running as a systemd service.")

if __name__ == "__main__":
    main()