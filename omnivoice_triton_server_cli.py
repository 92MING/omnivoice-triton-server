from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    package_dir = Path(__file__).resolve().parent / "omnivoice-triton-server"
    if str(package_dir) not in sys.path:
        sys.path.insert(0, str(package_dir))

    from launcher import main as launcher_main

    launcher_main()


if __name__ == "__main__":
    main()
