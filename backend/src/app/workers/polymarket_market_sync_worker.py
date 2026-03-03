from __future__ import annotations

import os
import time

from app.scripts.polymarket_sync_markets_and_map import main as sync_once

SYNC_SECONDS = int(os.getenv("POLY_MARKET_SYNC_SECONDS", "300"))


def main() -> None:
    print(f"✅ Polymarket market sync worker starting. interval={SYNC_SECONDS}s")

    while True:
        t0 = time.time()

        try:
            sync_once()
        except Exception as e:
            print("market sync error:", repr(e))

        elapsed = time.time() - t0
        time.sleep(max(0.0, SYNC_SECONDS - elapsed))


if __name__ == "__main__":
    main()
