"""Briefcase runs ``python -m checkersbot_ios`` on iOS, so this is the entry point."""

from .app import main

if __name__ == "__main__":
    main().main_loop()
