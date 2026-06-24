"""Order-Ahead Fulfillment Workflow (Temporal).

A single OrderWorkflow instance is the source of truth for one mobile order-ahead
order, orchestrating validate/price, inventory, payment, the barista queue, customer
pickup, and loyalty — surviving worker crashes via Temporal's durable replay.
"""

import os

# Temporal task queue the worker listens on and the starter targets.
TASK_QUEUE = "order-ahead"
# Temporal frontend address; the dev server listens on localhost:7233 by default.
TEMPORAL_ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "localhost:7233")
