# --------------------------------------------------------------------------------
# This file will carry out a load test and assertions on the "/api/v1/register"
# endpoint
# --------------------------------------------------------------------------------
from locust import HttpUser, task, constant_throughput
from src.trustmark.infra.commons import settings


class document(HttpUser):
    wait_time = constant_throughput(task_runs_per_second=5)

    @task
    def register(self):
        with open(file="tests/locust/testfile.txt", mode="r") as file:
            test_file = file

            with self.client.post(
                url=f"{settings.API_PREFIX}/register",
                catch_response=True,
                files={"file": test_file},
            ) as response:
                match response.status_code:
                    case 200:
                        response_body = response.json()

                        if response_body["stored"] is not True:
                            response.failure(
                                f"Unexpected response body 'stored' value. Expected True, received: {response_body['stored']}"
                            )

                        elif type(response_body["already_existed"]) is not bool:
                            response.failure(
                                "Unexpected response body 'already_existed' value type. Expected a boolean."
                            )

                        elif type(response_body["record_id"]) is not str:
                            response.failure(
                                "Unexpected response body 'record_id' value type. Expected a string."
                            )

                        elif type(response_body["created_at"]) is not str:
                            response.failure(
                                "Unexpected response body 'created_at' value type. Expected a string."
                            )

                        elif (
                            response_body["content_hash"]
                            != "51cd829961deef699d7099eb27a5b832c8c6c054407878d55917b490989cab55"
                        ):
                            response.failure(
                                f"Unexpected response body 'content_hash' value. Expected '51cd829961deef699d7099eb27a5b832c8c6c054407878d55917b490989cab55', received: {response_body['content_hash']}"
                            )

                        else:
                            response.success()

                    case _:
                        response.failure(
                            f"Unexpected response. {response.status_code}: {response.reason}. {response.text}"
                        )

    @task
    def register_no_file(self):
        with self.client.post(
            url=f"{settings.API_PREFIX}/register", catch_response=True, files={}
        ) as response:
            match response.status_code:
                case 422:
                    response.success()
                case _:
                    response.failure(
                        f"Unexpected response. {response.status_code}: {response.reason}. {response.text}"
                    )
