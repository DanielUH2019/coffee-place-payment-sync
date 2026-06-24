"""Order-Ahead Fulfillment Workflow (Temporal).

A single OrderWorkflow instance is the source of truth for one mobile order-ahead
order, orchestrating validate/price, inventory, payment, the barista queue, customer
pickup, and loyalty — surviving worker crashes via Temporal's durable replay.
"""
