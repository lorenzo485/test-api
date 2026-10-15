"""Application entry point."""

import time

from .config import get_settings


def main() -> None:
    """Run the application."""
    settings = get_settings()
    print(f"running in {settings.environment} at log level {settings.log_level}")
    while True:
        print("activo")
        time.sleep(3)


if __name__ == "__main__":
    main()
