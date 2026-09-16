"""Running ShipTrack. `uv run python -m shiptrack`."""

import uvicorn

from shiptrack.config import Settings


def main() -> None:
    settings = Settings()
    uvicorn.run("shiptrack.app:create_app", factory=True, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
