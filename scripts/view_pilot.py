"""Run with the verified project Python; never source the project .env."""

from agentic_payments_lab.pilot_viewer.launch import main

if __name__ == "__main__":
    raise SystemExit(main())
