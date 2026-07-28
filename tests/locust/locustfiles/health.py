# --------------------------------------------------------------------------------
# This file will carry out a load test and assertions on the "/api/v1/health"
# endpoint
# --------------------------------------------------------------------------------
from locust import HttpUser, task, constant_throughput
from src.trustmark.infra.commons import settings


class health_user(HttpUser):
    wait_time = constant_throughput(task_runs_per_second=5)
    network_timeout = 1
    max_retries = 5

    @task
    def health(self):
        with self.client.get(
            f"{settings.API_PREFIX}/health", catch_response=True
        ) as response:
            match response.status_code:
                case 200:
                    if response.json() == {"status": "ok"}:
                        response.success()
                    else:
                        response.failure("Unexpected response content")
                case _:
                    response.failure(f"{response.status_code}: {response.reason}")
