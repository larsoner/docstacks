"""Entry point for ``python -m docstacks``, for when the console script is not on PATH."""

from docstacks.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
