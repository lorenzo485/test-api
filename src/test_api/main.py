"""Application entry point."""

from .config import get_settings


def main() -> None:
    """Run the application."""
    settings = get_settings()
    print(f"running in {settings.environment} at log level {settings.log_level}")


if __name__ == "__main__":
    main()
