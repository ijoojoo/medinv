"""Verify the current Linux image and API contract against local PostgreSQL."""

import json
import subprocess

from dev import environment
from trial import ROOT, compatibility

CODE = """
import json, os, subprocess, time, urllib.request
assert os.getuid() == 10001
expected = json.loads(os.environ['MED_EXPECTED_API_OPERATIONS'])
methods = {'get', 'post', 'put', 'patch', 'delete', 'options', 'head', 'trace'}
process = subprocess.Popen(['python','-m','uvicorn','medinv.entrypoints.core_api:create_app',
                            '--factory','--host','127.0.0.1','--port','18000'])
try:
    for attempt in range(50):
        if process.poll() is not None:
            raise RuntimeError('API failed to start')
        try:
            with urllib.request.urlopen('http://127.0.0.1:18000/health/ready', timeout=2) as response:
                assert json.load(response)['status'] == 'ok'
            with urllib.request.urlopen('http://127.0.0.1:18000/openapi.json', timeout=2) as response:
                api = json.load(response)
            actual = {path: sorted(set(operations) & methods)
                      for path, operations in api['paths'].items()}
            assert actual == expected, 'Runtime API routes/methods differ from checked-in contract'
            assert api['info']['version'] == os.environ['MED_EXPECTED_API_VERSION']
            print(f'Linux image: non-root runtime, both databases and {len(actual)} API paths verified.')
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise RuntimeError('API readiness timeout')
finally:
    process.terminate()
    process.wait(timeout=10)
subprocess.run(['python','-m','medinv.entrypoints.expiry_worker','--once'],
               env={**os.environ, 'MED_RESERVATION_EXPIRY_ENABLED':'true'}, check=True)
print('Linux expiry worker: non-root runtime and bounded scan verified.')
"""


def main():
    config = compatibility()
    contract = json.loads(
        (ROOT / "contracts/openapi/implemented-core-openapi.json").read_text(
            encoding="utf-8"
        )
    )
    methods = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}
    expected = {
        path: sorted(set(operations) & methods)
        for path, operations in contract["paths"].items()
    }
    env = environment()
    for name in ("MED_DATABASE_URL", "MED_CONTROL_DATABASE_URL"):
        env[name] = env[name].replace("127.0.0.1:55432", "postgres:5432")
    env["MED_EXPECTED_API_OPERATIONS"] = json.dumps(expected)
    env["MED_EXPECTED_API_VERSION"] = contract["info"]["version"]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            config["project"] + "_default",
            "-e",
            "MED_DATABASE_URL",
            "-e",
            "MED_CONTROL_DATABASE_URL",
            "-e",
            "MED_EXPECTED_API_OPERATIONS",
            "-e",
            "MED_EXPECTED_API_VERSION",
            config["images"]["core"],
            "python",
            "-c",
            CODE,
        ],
        env=env,
        check=True,
    )


if __name__ == "__main__":
    main()
