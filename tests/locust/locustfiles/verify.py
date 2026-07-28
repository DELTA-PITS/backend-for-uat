# --------------------------------------------------------------------------------
# This file will carry out a load test and assertions on the "/api/v1/register"
# endpoint
#
# TODO: Make success not be dependent on the /register test succeeding.
#
# --------------------------------------------------------------------------------
from locust import HttpUser, constant_throughput, task
from src.trustmark.infra.commons import settings
from src.trustmark.infra.hash_engine import generate_hash


class verify_user(HttpUser):
    wait_time = constant_throughput(task_runs_per_second=5)

    @task
    def verify_with_path_parameter(self):
        with open(file="tests/locust/testfile.txt", mode="r") as file:
            hash = generate_hash(file.read())

            with self.client.get(
                url=f"{settings.API_PREFIX}/verify/{hash}",
                catch_response=True,
            ) as response:
                match response.status_code:
                    case 200:
                        response_body = response.json()

                        if response_body["valid"] is not True:
                            response.failure(
                                "Unexpected 'valid' value. Expected True, received: False"
                            )

                        elif response_body["issuer_id"] != "":
                            response.failure(
                                f"Unexpected 'issuer_id' value. Expected an empty string, received: {response_body['issuer_id']}"
                            )

                        elif response_body["record_id"] is not str:
                            response.failure(
                                f"Unexpected 'record_id' value type. Expected a string, received: {type(response_body['record_id'])}"
                            )

                        elif response_body["created_at"] is not str:
                            response.failure(
                                f"Unexpected 'created_at' value type. Expected a string, received: {type(response_body['created_at'])}"
                            )

                        elif response_body["content_hash"] != hash:
                            response.failure(
                                f"Unexpected 'content_hash' value. Expected {hash}, received: {response_body['content_hash']}"
                            )
                    case _:
                        response.failure(
                            f"Unexpected response. {response.status_code}: {response.reason}. {response.text}"
                        )

    @task
    def verify_with_file_body(self):
        with open(file="tests/locust/testfile.txt", mode="r") as file:
            file_content = file
            hash = "51cd829961deef699d7099eb27a5b832c8c6c054407878d55917b490989cab55"

            with self.client.post(
                url=f"{settings.API_PREFIX}/verify",
                catch_response=True,
                files={"file": file_content},
            ) as response:
                match response.status_code:
                    case 200:
                        response_body = response.json()

                        if response_body["valid"] is not True:
                            response.failure(
                                "Unexpected 'valid' value. Expected True, received: False"
                            )

                        elif response_body["content_hash"] != hash:
                            response.failure(
                                f"Unexpected 'content_hash' value. Expected {hash}, received: {response_body['content_hash']}"
                            )

                        else:
                            response.success()
                    case _:
                        response.failure(
                            f"Unexpected response. {response.status_code}: {response.reason}. {response.text}"
                        )

    @task
    def verify_not_stored(self):
        file_hash = "ThisIsNotAStoredHash"

        with self.client.get(
            url=f"{settings.API_PREFIX}/verify/{file_hash}",
            catch_response=True,
            data={},
        ) as response:
            match response.status_code:
                case 200:
                    response_body = response.json()

                    if response_body["valid"] is not False:
                        response.failure(
                            "Unexpected 'valid' value. Expected False, received: True"
                        )
                    elif response_body["content_hash"] != file_hash:
                        response.failure(
                            f"Unexpected 'content_hash' value. Expected {file_hash}, received: {response_body['content_hash']}"
                        )
                    else:
                        response.success()

                case _:
                    response.failure(
                        f"{response.status_code}: {response.reason}. {response.text}"
                    )
