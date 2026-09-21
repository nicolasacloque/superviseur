"""Le signal de relance réveille la boucle de découverte sans attendre l'intervalle."""

import asyncio
from typing import Any

from app.collector.discovery_trigger import DiscoveryTrigger
from app.common.bus import CHANNEL_DISCOVERY_RUN


async def test_a_published_request_wakes_the_wait(redis: Any) -> None:
    trigger = DiscoveryTrigger(redis)
    task = asyncio.create_task(trigger.run())
    try:
        await asyncio.sleep(0.2)  # le temps de s'abonner
        waiter = asyncio.create_task(trigger.wait(30))
        await asyncio.sleep(0.05)
        assert await redis.publish(CHANNEL_DISCOVERY_RUN, "run") == 1
        assert await asyncio.wait_for(waiter, 5) is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_the_wait_times_out_without_a_request(redis: Any) -> None:
    trigger = DiscoveryTrigger(redis)
    assert await trigger.wait(0.05) is False
