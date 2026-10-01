"""Compatibility entry point; application orchestration lives in lowball.engine."""

from lowball.engine import main

__all__ = ["main"]

if __name__ == "__main__":
    raise SystemExit(main())
