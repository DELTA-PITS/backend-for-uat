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

                        # PASS 5 (2026-09-14) fix - see _docs/qa/pass5/ report,
                        # "Section 9 / test-code findings" for the original
                        # (defective) assertions this replaces:
                        #   - `is not str` / `is not str` compared a VALUE to
                        #     the TYPE OBJECT `str`, which is always True for
                        #     any string instance - these branches fired
                        #     unconditionally as failures whenever reached,
                        #     which (given issuer_id was also checked against
                        #     a hardcoded "must be empty" expectation) meant
                        #     historical benchmark "failure" counts likely
                        #     included requests that returned a fully correct
                        #     200 response.
                        #   - `issuer_id != ""` baked in the known Finding-2
                        #     bug (missing Keycloak `sub` claim -> empty
                        #     issuer_id) as the EXPECTED/correct value. Fixed
                        #     to assert the intended correct behaviour
                        #     (non-empty issuer_id) instead - this will keep
                        #     failing for as long as Finding 2 is unfixed,
                        #     which is accurate, not a test bug.
                        if response_body["valid"] is not True:
                            response.failure(
                                "Unexpected 'valid' value. Expected True, received: False"
                            )

                        elif not response_body.get("issuer_id"):
                            response.failure(
                                f"issuer_id is empty/missing (Finding 2 - Keycloak token has no "
                                f"'sub' claim). Intended correct behaviour is a non-empty "
                                f"publisher identity. Received: {response_body.get('issuer_id')!r}"
                            )

                        elif not isinstance(response_body["record_id"], str):
                            response.failure(
                                f"Unexpected 'record_id' value type. Expected a string, received: {type(response_body['record_id'])}"
                            )

                        elif not isinstance(response_body["created_at"], str):
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
        # PASS 5 (2026-09-14) fix: the original literal "ThisIsNotAStoredHash"
        # is not a 64-char hex string, so verify_by_hash()'s own format
        # validation rejects it with 400 BEFORE ever reaching the "not
        # found" branch this task intends to exercise - every request here
        # was a guaranteed, permanent 100% failure that had nothing to do
        # with whether a hash is registered or not. Replaced with a
        # well-formed but never-registered 64-hex-char hash.
        file_hash = "ff" * 32

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
