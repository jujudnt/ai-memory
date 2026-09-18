from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] != "--background":
        from aimemory.cli import main as cli_main

        return cli_main(sys.argv[1:])

    from aimemory.desktop import main as desktop_main

    return desktop_main(background="--background" in sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
