# Engineering at Northwind: how our ingestion pipeline works

Store data arrives through our public API, which is written in Python with FastAPI. Each upload is validated, stored, and published as an event to Kafka. Downstream consumers clean the data and write it into PostgreSQL, and hot dashboards are served from a Redis cache.

Internally, deployments are orchestrated by Hermes, our in-house release tool, and we are migrating some batch jobs to Apache Flink.

Next year we plan to launch Northwind Forecast Pro, a new product for promotion planning, which is currently in private beta.
