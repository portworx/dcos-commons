'''Utilities relating to running commands and HTTP requests

************************************************************************
FOR THE TIME BEING WHATEVER MODIFICATIONS ARE APPLIED TO THIS FILE
SHOULD ALSO BE APPLIED TO sdk_cmd IN ANY OTHER PARTNER REPOS
************************************************************************
'''
import json as jsonlib
import os
import logging
import retrying
import subprocess
import traceback
import urllib.parse

import dcos.errors
import dcos.http
import shakedown
import sdk_utils


log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30 * 60


def service_request(
        method,
        service_name,
        service_path,
        retry=True,
        raise_on_error=True,
        log_args=True,
        verify=None,
        timeout_seconds=60,
        **kwargs):
    """Used to query a service running on the cluster. See `cluster_request()` for arg meanings.
    :param service_name: The name of the service, e.g. 'marathon' or 'hello-world'
    :param service_path: HTTP path to be queried against the service, e.g. '/v2/apps'. Leading slash is optional.
    """
    # Sanitize leading slash on service_path before calling urljoin() to avoid this:
    # 'http://example.com/service/myservice/' + '/v1/rpc' = 'http://example.com/v1/rpc'
    cluster_path = urllib.parse.urljoin('/service/{}/'.format(service_name), service_path.lstrip('/'))
    return cluster_request(method, cluster_path, retry, raise_on_error, log_args, verify, timeout_seconds, **kwargs)


def cluster_request(
        method,
        cluster_path,
        retry=True,
        raise_on_error=True,
        log_args=True,
        verify=None,
        timeout_seconds=60,
        **kwargs):
    """Queries the provided cluster HTTP path using the provided method, with the following handy features:
    - The DCOS cluster's URL is automatically applied to the provided path.
    - Auth headers are automatically added.
    - If the response code is >=400, optionally retries and/or raises a `requests.exceptions.HTTPError`.

    :param method: Method to use for the query, such as `GET`, `POST`, `DELETE`, or `PUT`.
    :param cluster_path: HTTP path to be queried on the cluster, e.g. `/marathon/v2/apps`. Leading slash is optional.
    :param retry: Whether to retry the request automatically if an HTTP error (>=400) is returned.
    :param raise_on_error: Whether to raise a `requests.exceptions.HTTPError` if the response code is >=400.
                           Disabling this effectively implies `retry=False` where HTTP status is concerned.
    :param log_args: Whether to log the contents of `kwargs`. Can be disabled to reduce noise.
    :param verify: Whether to verify the TLS certificate returned by the cluster, or a path to a certificate file.
    :param kwargs: Additional arguments to requests.request(), such as `json={"example": "content"}`
                   or `params={"example": "param"}`.
    :rtype: requests.Response
    """

    url = shakedown.dcos_url_path(cluster_path)
    cluster_path = '/' + cluster_path.lstrip('/')  # consistently include slash prefix for clearer logging below

    def fn():
        # Underlying dcos.http.request will wrap responses in custom exceptions. This messes with
        # our ability to handle the situation when an error occurs, so unwrap those exceptions.
        try:
            response = dcos.http.request(method, url, verify=verify, **kwargs)
        except dcos.errors.DCOSHTTPException as e:
            # DCOSAuthenticationException, DCOSAuthorizationException, DCOSBadRequest, DCOSHTTPException
            response = e.response
        except dcos.errors.DCOSUnprocessableException as e:
            # unlike the the above, this directly extends DCOSHTTPException
            response = e.response
        log_msg = 'Got {} for {} {}'.format(response.status_code, method.upper(), cluster_path)
        if kwargs:
            # log arg content (or just arg names, with hack to avoid 'dict_keys([...])') if present
            log_msg += ' (args: {})'.format(kwargs if log_args else [e for e in kwargs.keys()])
        log.info(log_msg)
        if not response.ok:
            # Query failed (>= 400). Before (potentially) throwing, print response payload which may
            # include additional error details.
            response_text = response.text
            if response_text:
                log.info('Response content ({} bytes):\n{}'.format(len(response_text), response_text))
            else:
                log.info('No response content')
        if raise_on_error:
            response.raise_for_status()
        return response

    if retry:
        # Use wrapper to implement retry:
        @retrying.retry(
            wait_fixed=1000,
            stop_max_delay=timeout_seconds*1000)
        def retry_fn():
            return fn()
        return retry_fn()
    else:
        # No retry, invoke directly:
        return fn()


def svc_cli(package_name, service_name, service_cmd, json=False, print_output=True, return_stderr_in_stdout=False):
    full_cmd = '{} --name={} {}'.format(package_name, service_name, service_cmd)

    if not json:
        return run_cli(full_cmd, print_output=print_output, return_stderr_in_stdout=return_stderr_in_stdout)
    else:
        # TODO(elezar): We shouldn't use json=True and return_stderr_in_stdout=True together
        # assert not return_stderr_in_stdout, json=True and return_stderr_in_stdout=True should not be used together
        return get_json_output(full_cmd, print_output=print_output)


def run_raw_cli(cmd, print_output=True):
    """Runs the command with `dcos` as the prefix to the shell command
    and returns the resulting output (stdout seperated from stderr by a newline).

    eg. `cmd`= "package install <package-name>" results in:
    $ dcos package install <package-name>
    """
    dcos_cmd = "dcos {}".format(cmd)
    result = subprocess.run([dcos_cmd], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout = ""
    stderr = ""

    if result.stdout:
        stdout = result.stdout.decode('utf-8').strip()

    if result.stderr:
        stderr = result.stderr.decode('utf-8').strip()

    if print_output:
        if stdout:
            log.info("STDOUT:\n{}".format(stdout))
        if stderr:
            log.info("STDERR:\n{}".format(stderr))

    return result.returncode, stdout, stderr

# Added simple function to run OS system command
def run_system_cmd(cmd):
    return os.system(cmd)

def run_cli(cmd, print_output=True, return_stderr_in_stdout=False):

    _, stdout, stderr = run_raw_cli(cmd, print_output)

    if return_stderr_in_stdout:
        return stdout + "\n" + stderr

    return stdout


def kill_task_with_pattern(pattern, agent_host=None, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    @retrying.retry(
        wait_fixed=1000,
        stop_max_delay=timeout_seconds*1000,
        retry_on_result=lambda res: not res)
    def fn():
        command = (
            "sudo kill -9 "
            "$(ps ax | grep {} | grep -v grep | tr -s ' ' | sed 's/^ *//g' | "
            "cut -d ' ' -f 1)".format(pattern))
        if agent_host is None:
            exit_status, _ = shakedown.run_command_on_master(command)
        else:
            exit_status, _ = shakedown.run_command_on_agent(agent_host, command, 'vagrant', '/ssh/key')

        return exit_status

    # might not be able to connect to the agent on first try so we repeat until we can
    fn()


@retrying.retry(stop_max_attempt_number=3,
                wait_fixed=1000,
                retry_on_result=lambda result: not result)
def create_task_text_file(task_name: str, filename: str, lines: list) -> bool:
    output_cmd = """bash -c \"cat >{output_file} << EOL
{content}
EOL\"""".format(output_file=filename, content="\n".join(lines))
    log.info("Running: %s", output_cmd)
    rc, stdout, stderr = task_exec(task_name, output_cmd)

    if rc or stderr:
        log.error("Error creating file %s. rc=%s stdout=%s stderr=%s", filename, rc, stdout, stderr)
        return False

    linecount_cmd = "wc -l {output_file}".format(output_file=filename)
    rc, stdout, stderr = task_exec(task_name, linecount_cmd)

    if rc or stderr:
        log.error("Error checking file %s. rc=%s stdout=%s stderr=%s", filename, rc, stdout, stderr)
        return False

    written_lines = 0
    try:
        written_lines = int(stdout.split(" ")[0])
    except Exception as e:
        log.error(e)

    expected_lines = len("\n".join(lines).split("\n"))
    if written_lines != expected_lines:
        log.error("Number of written lines do not match. stdout=%s expected=%s written=%s",
                  stdout, expected_lines, written_lines)
        return False

    return True


def shutdown_agent(agent_ip, timeout_seconds=DEFAULT_TIMEOUT_SECONDS):
    @retrying.retry(
        wait_fixed=1000,
        stop_max_delay=timeout_seconds*1000,
        retry_on_result=lambda res: not res)
    def fn():
        ok, stdout = shakedown.run_command_on_agent(agent_ip, 'sudo shutdown -h +1', 'vagrant', '/ssh/key')
        log.info('Shutdown agent {}: ok={}, stdout="{}"'.format(agent_ip, ok, stdout))
        return ok
    # Might not be able to connect to the agent on first try so we repeat until we can
    fn()

    # We use a manual check to detect that the host is down. Mesos takes ~5-20 minutes to detect a
    # dead agent, so relying on Mesos to tell us this isn't really feasible for a test.

    log.info('Waiting for agent {} to appear inactive in /mesos/slaves'.format(agent_ip))

    @retrying.retry(
        wait_fixed=1000,
        stop_max_delay=5*60*1000,
        retry_on_result=lambda res: res)
    def wait_for_unresponsive_agent():
        try:
            response = cluster_request('GET', '/mesos/slaves', retry=False).json()
            agent_statuses = {}
            for agent in response['slaves']:
                agent_statuses[agent['hostname']] = agent['active']
            log.info('Wait for {}=False: {}'.format(agent_ip, agent_statuses))
            # If no agents were found, try again
            if len(agent_statuses) == 0:
                return True
            # If other agents are listed, but not OUR agent, assume that OUR agent is now inactive.
            # (Shouldn't happen, but just in case...)
            return agent_statuses.get(agent_ip, False)
        except:
            log.info(traceback.format_exc())
            # Try again. Wait for the ip to be definitively inactive.
            return True

    wait_for_unresponsive_agent()

    log.info('Agent {} appears inactive in /mesos/slaves, proceeding.'.format(agent_ip))


def task_exec(task_name: str, cmd: str, return_stderr_in_stdout: bool = False) -> tuple:
    """
    Invokes the given command on the task via `dcos task exec`.
    :param task_name: Name of task to run command on.
    :param cmd: The command to execute.
    :return: a tuple consisting of the task exec's return code, stdout, and stderr
    """

    if cmd.startswith("./") and sdk_utils.dcos_version_less_than("1.10"):
        full_cmd = os.path.join(get_task_sandbox_path(task_name), cmd)

        if cmd.startswith("./bootstrap"):
            # On 1.9 we need to set LIB_PROCESS_IP for bootstrap
            full_cmd = "bash -c \"LIBPROCESS_IP=0.0.0.0 {}\"".format(full_cmd)
    else:
        full_cmd = cmd

    exec_cmd = "task exec {task_name} {cmd}".format(task_name=task_name, cmd=full_cmd)
    rc, stdout, stderr = run_raw_cli(exec_cmd)

    if return_stderr_in_stdout:
        return rc, stdout + "\n" + stderr

    return rc, stdout, stderr


def get_json_output(cmd, print_output=True):
    _, stdout, stderr = run_raw_cli(cmd, print_output)

    if stderr:
        log.warning("stderr for command '%s' is non-empty: %s", cmd, stderr)

    try:
        json_stdout = jsonlib.loads(stdout)
    except Exception as e:
        log.warning("Error converting stdout to json:\n%s", stdout)
        raise e

    return json_stdout


def get_task_sandbox_path(task_name: str) -> str:
    task_info = get_task_info(task_name)

    if task_info:

        executor_path = task_info["executor_id"]
        if not executor_path:
            executor_path = task_info["id"]
        # Assume the latest run:
        return os.path.join("/var/lib/mesos/slave/slaves", task_info["slave_id"],
                            "frameworks", task_info["framework_id"],
                            "executors", executor_path,
                            "runs/latest")

    return ""


@retrying.retry(stop_max_attempt_number=3, wait_fixed=2000)
def get_task_info(task_name: str) -> dict:
    """
    :return (dict): Get the task information for the specified task
    """
    log.info("Getting task information")
    raw_tasks = run_cli("task {task_name} --json".format(task_name=task_name))
    if raw_tasks:
        tasks = jsonlib.loads(raw_tasks)
        for task in tasks:
            if task["name"] == task_name:
                log.info("Matched on 'name'")
                return task
            if task.get("id", None) == task_name:
                log.info("Matched on 'id'")
                return task

    log.error("Task %s not found.\nFound: %s", task_name, raw_tasks)
    return {}
