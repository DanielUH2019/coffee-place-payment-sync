"""Temporal worker for the Order-Ahead workflow.

Registers OrderWorkflow + the activities on the `order-ahead` task queue. Activities
are sync (they do blocking HTTP / in-memory work), so we run them in a thread pool.

Kill this process mid-order and start it again to demo F6: Temporal replays the
workflow history and resumes at the same state, with no double charge.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.client import Client
from temporalio.worker import Worker

from . import TASK_QUEUE, TEMPORAL_ADDRESS
from .activities import ALL_ACTIVITIES
from .workflow import OrderWorkflow


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    client = await Client.connect(TEMPORAL_ADDRESS)
    with ThreadPoolExecutor(max_workers=20) as executor:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[OrderWorkflow],
            activities=ALL_ACTIVITIES,
            activity_executor=executor,
        )
        logging.getLogger("orders.worker").info(
            "worker up on %s task-queue=%s", TEMPORAL_ADDRESS, TASK_QUEUE
        )
        await worker.run()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
