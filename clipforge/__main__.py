"""Start the single-machine ClipForge server."""

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "clipforge.app:app",
        host=os.environ.get("CLIPFORGE_HOST", "127.0.0.1"),
        port=int(os.environ.get("CLIPFORGE_PORT", "8765")),
        workers=1,  # In-process queue requires exactly one worker.
    )


if __name__ == "__main__":
    main()
