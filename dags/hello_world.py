from __future__ import annotations

import logging

import pendulum
from airflow.sdk import dag, task


@dag(
    dag_id="hello_world",
    description="Hello, World! example DAG",
    schedule="@daily",
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    catchup=False,
    default_args={"owner": "airflow"},
    tags=["example", "hello"],
)
def hello_world() -> None:
    @task(task_id="say_hello")
    def say_hello() -> None:
        logger = logging.getLogger("airflow.task")
        logger.info("Hello, World!")

    say_hello()


hello_world_dag = hello_world()
