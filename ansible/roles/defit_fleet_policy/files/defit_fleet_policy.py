#!/usr/bin/env python3
"""Apply the DEFIT Fleet policy using Kibana API requests proven in the lab.

Only Python standard-library dependencies are used. Credentials are read from the
existing local Elastic container environment file, never command-line arguments.
"""

import argparse
import base64
from copy import deepcopy
import json
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


class FleetError(RuntimeError):
    """A user-actionable Fleet API or configuration failure."""


def progress(stage, message):
    print(f"[DEFIT] {stage}: {message}", file=sys.stderr, flush=True)


def load_password(environment_file):
    path = Path(environment_file)
    if not path.is_file():
        raise FleetError(f"Elastic environment file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        key, separator, value = stripped.partition("=")
        if separator and key.strip() == "ELASTIC_PASSWORD":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
            break
    raise FleetError(f"ELASTIC_PASSWORD missing or empty in {path}")


def sanitized_error(raw, password):
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return "Fleet returned a non-JSON error; response body withheld"
    if not isinstance(obj, dict):
        return "Fleet returned an unexpected error response"
    summary = ": ".join(str(obj[k]) for k in ("error", "message") if obj.get(k))
    if not summary:
        summary = f"Fleet error status {obj.get('statusCode', 'unknown')}"
    if password:
        summary = summary.replace(password, "[REDACTED]")
    summary = re.sub(r"(?i)(authorization|api[_-]?key|password|token|secret)\s*[:=]\s*[^\s,}]+", r"\1=[REDACTED]", summary)
    summary = re.sub(r"(?i)Bearer\s+\S+", "Bearer [REDACTED]", summary)
    return summary[:1500]


class FleetClient:
    def __init__(self, base_url, username, password, verify_tls=False, timeout=45):
        self.base_url = base_url.rstrip("/")
        self.password = password
        auth = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.headers = {
            "Authorization": "Basic " + auth,
            "kbn-xsrf": "true",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        self.timeout = timeout
        self.context = ssl.create_default_context() if verify_tls else ssl._create_unverified_context()

    def api(self, method, path, payload=None):
        if not path.startswith("/"):
            raise FleetError("Invalid Fleet API path")
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(self.base_url + path, data=data, headers=self.headers, method=method)
        try:
            with urlopen(request, context=self.context, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
                return json.loads(body) if body.strip() else {}
        except HTTPError as exc:
            raw = exc.read(8192).decode("utf-8", errors="replace")
            raise FleetError(
                f"Fleet {method} {path} failed (HTTP {exc.code}): {sanitized_error(raw, self.password)}"
            ) from None
        except (URLError, TimeoutError, OSError) as exc:
            raise FleetError(f"Fleet {method} {path} connection failed ({type(exc).__name__})") from None
        except ValueError:
            raise FleetError(f"Fleet {method} {path} returned invalid JSON") from None

    def paginated(self, path):
        items = []
        for page in range(1, 51):
            separator = "&" if "?" in path else "?"
            response = self.api("GET", f"{path}{separator}{urlencode({'page': page, 'perPage': 100})}")
            block = response.get("items", [])
            if not isinstance(block, list):
                raise FleetError(f"Fleet list API returned invalid items for {path}")
            items.extend(block)
            if len(block) < 100 or len(items) >= response.get("total", 1 << 30):
                return items
        raise FleetError(f"Fleet paginated result exceeded 5000 entries: {path}")


def choose_policy(policies, old_name, new_name):
    matched = [p for p in policies if p.get("name") in (old_name, new_name)]
    if len(matched) != 1:
        names = ", ".join(sorted(str(p.get("name")) for p in matched)) or "none"
        raise FleetError(f"Expected exactly one old/new Fleet agent policy, found {len(matched)} ({names})")
    p = matched[0]
    if not p.get("id"):
        raise FleetError("Fleet agent policy missing ID")
    if p.get("is_managed"):
        raise FleetError("Refusing to rename managed Fleet agent policy")
    return p


def get_package_version(client, name, version):
    if version != "auto":
        return version
    data = client.api("GET", "/api/fleet/epm/packages/" + quote(name, safe=""))
    item = data.get("item") or {}
    if data.get("status") == "installed":
        version = item.get("version")
    else:
        version = data.get("latestVersion") or item.get("latestVersion") or item.get("version")
    if not version:
        raise FleetError("Fleet did not report a usable version for package " + name)
    return version


def ensure_package(client, name, version, dry_run):
    endpoint = "/api/fleet/epm/packages/" + quote(name, safe="") + "/" + quote(version, safe="")
    result = client.api("GET", endpoint)
    status = result.get("status") or (result.get("item") or {}).get("status")
    if status == "installed":
        progress(name, f"Package {version} already installed")
        return False
    if dry_run:
        progress(name, f"Would install package {version} (current status: {status})")
        return True
    progress(name, f"Installing package {version} (current status: {status})")
    client.api("POST", endpoint, {})
    for attempt in range(12):
        result = client.api("GET", endpoint)
        status = result.get("status") or (result.get("item") or {}).get("status")
        if status == "installed":
            progress(name, f"Package {version} installed")
            return True
        if status in ("install_failed", "failed"):
            raise FleetError(f"Package installation reported status {status} for {name} {version}")
        if attempt != 11:
            time.sleep(3)
    raise FleetError(f"Package {name} {version} did not reach installed status (last: {status})")


def get_template(client, name, version):
    endpoint = "/api/fleet/epm/templates/" + quote(name, safe="") + "/" + quote(version, safe="") + "/inputs"
    result = client.api("GET", endpoint)
    inputs = result.get("inputs", [])
    if not isinstance(inputs, list) or not inputs:
        raise FleetError(f"No input templates returned for {name} {version}")
    return inputs


def template_input(inputs, input_type, required_id):
    matches = [i for i in inputs if i.get("type") == input_type]
    if len(matches) != 1 or matches[0].get("id") != required_id:
        observed = [(i.get("type"), i.get("id")) for i in inputs]
        raise FleetError(f"Expected input {input_type} ID {required_id}; template has {observed}")
    return matches[0]


def datasets_for_input(input_info):
    streams = input_info.get("streams", [])
    return {
        stream.get("data_stream", {}).get("dataset")
        for stream in streams if isinstance(stream.get("data_stream"), dict)
    }


def common_payload(name, description, pkg, version, policy_id, namespace, condition):
    # The simplified Fleet API does NOT permit top-level 'enabled'.
    return {
        "name": name,
        "description": description,
        "namespace": namespace,
        "policy_ids": [policy_id],
        "condition": condition,
        "package": {"name": pkg, "version": version},
    }


def docker_payload(inputs, version, policy_id, namespace, condition):
    metrics = template_input(inputs, "docker/metrics", "docker-docker/metrics")
    filestream = template_input(inputs, "filestream", "docker-filestream")
    if "docker.container_logs" not in datasets_for_input(filestream):
        raise FleetError("Docker template missing docker.container_logs stream")
    payload = common_payload(
        "DEFIT-Docker-ECS", "Docker metrics and ECS container logs for PHP2Shell",
        "docker", version, policy_id, namespace, condition,
    )
    payload["inputs"] = {
        metrics["id"]: {"enabled": True},
        filestream["id"]: {
            "enabled": True,
            "streams": {"docker.container_logs": {"enabled": True}},
        },
    }
    return payload


def packet_payload(inputs, version, policy_id, namespace, condition, interface, http_ports):
    packet = template_input(inputs, "packet", "network-packet")
    datasets = datasets_for_input(packet)
    if not {"network_traffic.http", "network_traffic.flow"}.issubset(datasets):
        raise FleetError(f"Network packet template missing required HTTP/flow streams ({sorted(datasets)})")
    if not all(isinstance(port, int) and 1 <= port <= 65535 for port in http_ports):
        raise FleetError("Invalid HTTP ports; expected integers 1-65535")
    # All other protocol streams are explicitly disabled, including any new streams.
    streams = {dataset: {"enabled": False} for dataset in sorted(datasets) if dataset}
    streams["network_traffic.http"] = {
        "enabled": True,
        "vars": {
            "port": http_ports,
            "monitor_processes": True,
            "send_request": True,
            "send_response": True,
            "send_all_headers": True,
        },
    }
    streams["network_traffic.flow"] = {
        "enabled": True,
        "vars": {"geoip_enrich": False},
    }
    payload = common_payload(
        "DEFIT-Network-Packet-Capture", "PHP2Shell HTTP traffic and network flow monitoring",
        "network_traffic", version, policy_id, namespace, condition,
    )
    payload["inputs"] = {
        packet["id"]: {"enabled": True, "vars": {"interface": interface}, "streams": streams},
    }
    return payload


def subset(expected, actual):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and subset(v, actual[k]) for k, v in expected.items())
    if isinstance(expected, list):
        return expected == actual
    return expected == actual


def comparable_package_policy(actual, desired):
    # Existing package policies get Fleet-managed defaults; ignore extra fields.
    if not isinstance(actual, dict):
        return False
    actual_ids = actual.get("policy_ids") or ([actual.get("policy_id")] if actual.get("policy_id") else [])
    return (
        actual.get("name") == desired["name"]
        and actual.get("namespace") == desired["namespace"]
        and actual.get("condition") == desired["condition"]
        and actual_ids == desired["policy_ids"]
        and subset(desired["package"], actual.get("package", {}))
        and subset(desired["inputs"], actual.get("inputs", {}))
    )


def apply_package_policy(client, desired, existing, dry_run):
    name = desired["name"]
    matching = [item for item in existing if item.get("name") == name]
    if len(matching) > 1:
        raise FleetError(f"Multiple integration policies named {name}; refusing ambiguous update")
    if matching:
        item = matching[0]
        identifier = item.get("id")
        if not identifier:
            raise FleetError(f"Existing integration {name} has no ID")
        path = "/api/fleet/package_policies/" + quote(identifier, safe="") + "?format=simplified"
        actual = client.api("GET", path).get("item", {})
        if not isinstance(actual.get("inputs"), dict):
            raise FleetError(f"Existing {name} does not have a readable simplified inputs object")
        if comparable_package_policy(actual, desired):
            progress(name, f"Already matches desired settings (integration ID {identifier})")
            return False
        current_ids = actual.get("policy_ids") or [actual.get("policy_id")]
        if current_ids != desired["policy_ids"]:
            raise FleetError(f"Existing {name} belongs to another agent policy; refusing reassignment")
        if dry_run:
            progress(name, f"Would update integration ID {identifier} due to configuration drift")
            return True
        progress(name, f"Updating existing integration ID {identifier} to approved settings")
        client.api("PUT", path, desired)
    else:
        if dry_run:
            progress(name, "Would create integration policy")
            return True
        progress(name, "Creating integration policy with live-tested simplified request body")
        result = client.api("POST", "/api/fleet/package_policies?format=simplified", desired)
        identifier = (result.get("item") or {}).get("id")
        if not identifier:
            raise FleetError(f"Fleet did not return an integration ID for {name}")
        path = "/api/fleet/package_policies/" + quote(identifier, safe="") + "?format=simplified"
    actual = client.api("GET", path).get("item", {})
    if not comparable_package_policy(actual, desired):
        raise FleetError(f"Fleet saved {name} but the readback differs from the requested settings")
    progress(name, f"Verified saved integration ID {identifier}")
    return True


# Popup names and OS-specific coverage observed in Elastic Defend 9.5.0.
DEFEND_POPUP_TYPES = {
    "windows": ("malware", "ransomware", "memory_protection", "behavior_protection"),
    "mac": ("malware", "behavior_protection", "memory_protection"),
    "linux": ("malware", "behavior_protection", "memory_protection"),
}


def defend_popup_update_payload(item, old_prefix="Ludus - ", new_prefix="DEFIT - "):
    """Return the live-tested legacy PUT schema, replacing only popup message prefixes.

    Build from the current GET response so future deployments keep their own package
    policy ID, ES version, assigned agent policy and artifact manifest unchanged.
    """
    required = ("version", "name", "namespace", "description", "package", "enabled",
                "policy_id", "policy_ids", "inputs")
    missing = [key for key in required if key not in item]
    if missing:
        raise FleetError("Elastic Defend policy missing required legacy PUT fields: " + ", ".join(missing))
    payload = {key: deepcopy(item[key]) for key in required}
    inputs = payload["inputs"]
    if not isinstance(inputs, list):
        raise FleetError("Elastic Defend legacy policy inputs must be an array")
    endpoint_inputs = [i for i in inputs if isinstance(i, dict) and i.get("type") == "endpoint"]
    if len(endpoint_inputs) != 1:
        raise FleetError(f"Expected one Elastic Defend endpoint input; found {len(endpoint_inputs)}")
    try:
        values = endpoint_inputs[0]["config"]["policy"]["value"]
    except (KeyError, TypeError) as exc:
        raise FleetError("Elastic Defend policy.value not found in endpoint input") from exc

    changed = []
    for platform, popup_names in DEFEND_POPUP_TYPES.items():
        try:
            popups = values[platform]["popup"]
        except (KeyError, TypeError) as exc:
            raise FleetError(f"Elastic Defend {platform} popup configuration missing") from exc
        for popup_name in popup_names:
            popup = popups.get(popup_name)
            if not isinstance(popup, dict) or not isinstance(popup.get("message"), str):
                raise FleetError(f"Elastic Defend {platform}.{popup_name} message missing")
            current = popup["message"]
            if current.startswith(old_prefix):
                popup["message"] = new_prefix + current[len(old_prefix):]
                changed.append(platform + "." + popup_name)
            elif not current.startswith(new_prefix):
                # Don't overwrite arbitrary custom messages or silently declare success.
                raise FleetError(f"Unexpected Elastic Defend popup prefix at {platform}.{popup_name}")
    return payload, changed


def apply_defend_popup_update(client, integration_name, dry_run=False):
    """Find the Defend integration by name, retain configuration, change popup text."""
    progress("DEFEND", f"Looking up integration {integration_name} (legacy Fleet format)")
    policies = client.paginated("/api/fleet/package_policies?format=legacy")
    named = [p for p in policies if p.get("name") == integration_name]
    if len(named) != 1:
        raise FleetError(f"Expected exactly one integration named {integration_name}, found {len(named)}")
    summary = named[0]
    if (summary.get("package") or {}).get("name") != "endpoint":
        raise FleetError(f"Integration {integration_name} is not the Elastic Defend endpoint package")
    identifier = summary.get("id")
    if not identifier:
        raise FleetError(f"Elastic Defend integration {integration_name} missing ID")
    path = "/api/fleet/package_policies/" + quote(identifier, safe="") + "?format=legacy"
    original = client.api("GET", path).get("item", {})
    if original.get("name") != integration_name or (original.get("package") or {}).get("name") != "endpoint":
        raise FleetError("Elastic Defend integration identity changed during read; refusing PUT")
    if original.get("id") != identifier:
        raise FleetError("Elastic Defend integration ID changed during read; refusing PUT")
    payload, changed = defend_popup_update_payload(original)
    if not changed:
        progress("DEFEND", "All 10 popup messages already use the DEFIT prefix; no change")
        return False
    progress("DEFEND", f"Preparing {len(changed)} popup message updates on integration {identifier}")
    if dry_run:
        progress("DEFEND", "Would update: " + ", ".join(changed))
        return True
    client.api("PUT", path, payload)
    after = client.api("GET", path).get("item", {})
    desired, remaining = defend_popup_update_payload(after)
    if remaining:
        raise FleetError("Elastic Defend popup update was accepted but the readback still contains Ludus prefixes")
    if after.get("id") != identifier:
        raise FleetError("Elastic Defend integration ID was unexpectedly changed by Fleet")
    progress("DEFEND", f"Verified {len(DEFEND_POPUP_TYPES['windows']) + len(DEFEND_POPUP_TYPES['mac']) + len(DEFEND_POPUP_TYPES['linux'])} popup messages on integration {identifier}")
    return True


def run(client, old_name, new_name, docker_version, packet_version, condition, interface, http_ports, dry_run=False, defend_integration_name="elastic-defend-1"):
    progress("START", "Reading Fleet agent policies")
    policies = client.paginated("/api/fleet/agent_policies")
    policy = choose_policy(policies, old_name, new_name)
    policy_id = policy["id"]
    namespace = policy.get("namespace") or "default"
    progress("POLICY", f"Found {policy['name']} (ID {policy_id}, namespace {namespace})")
    changes = []
    if policy["name"] != new_name:
        if dry_run:
            progress("RENAME", "Would rename agent policy while preserving ID and namespace")
        else:
            progress("RENAME", f"Renaming to {new_name} while preserving ID and namespace")
            client.api("PUT", "/api/fleet/agent_policies/" + quote(policy_id, safe=""),
                       {"name": new_name, "namespace": namespace})
        changes.append("rename_policy")

    existing = client.paginated("/api/fleet/package_policies?format=simplified")
    for name, version_choice, builder in (
        ("docker", docker_version, docker_payload),
        ("network_traffic", packet_version, packet_payload),
    ):
        version = get_package_version(client, name, version_choice)
        progress(name, f"Using package version {version}")
        # A dry run still validates available templates, but does not make changes.
        installed = ensure_package(client, name, version, dry_run)
        if installed:
            changes.append("install_" + name)
        template = get_template(client, name, version)
        if name == "docker":
            desired = builder(template, version, policy_id, namespace, condition)
        else:
            desired = builder(template, version, policy_id, namespace, condition, interface, http_ports)
        if apply_package_policy(client, desired, existing, dry_run):
            changes.append("apply_" + name)

    if apply_defend_popup_update(client, defend_integration_name, dry_run):
        changes.append("update_defend_popups")

    if not dry_run:
        new_policies = client.paginated("/api/fleet/agent_policies")
        final = [p for p in new_policies if p.get("id") == policy_id]
        if len(final) != 1 or final[0].get("name") != new_name:
            raise FleetError("Final Fleet agent policy verification failed")
    progress("DONE", "Fleet policy verified" if not dry_run else "Dry-run checks completed")
    return {
        "changed": bool(changes) and not dry_run,
        "would_change": bool(changes),
        "policy_id": policy_id,
        "policy_name": new_name,
        "actions": changes,
    }


def cli():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-path", default="/opt/elastic_container")
    parser.add_argument("--url", default="https://127.0.0.1:5601")
    parser.add_argument("--old-name", default="Ludus-Agent-Policy")
    parser.add_argument("--new-name", default="DEFIT-Agent-Policy")
    parser.add_argument("--docker-version", default="2.16.1")
    parser.add_argument("--network-version", default="2.0.0")
    parser.add_argument("--condition", default="${host.platform} == 'linux' and match(${host.name}, '(?i).*php2shell.*')")
    parser.add_argument("--capture-interface", default="any")
    parser.add_argument("--defend-integration-name", default="elastic-defend-1")
    parser.add_argument("--http-ports", default="80")
    parser.add_argument("--verify-tls", default="false")
    parser.add_argument("--dry-run", default="false")
    args = parser.parse_args()
    try:
        ports = [int(value.strip()) for value in args.http_ports.split(",")]
        password = load_password(Path(args.install_path) / ".env")
        client = FleetClient(args.url, "elastic", password, args.verify_tls.lower() == "true")
        result = run(
            client, args.old_name, args.new_name, args.docker_version, args.network_version,
            args.condition, args.capture_interface, ports, args.dry_run.lower() == "true",
            args.defend_integration_name,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (FleetError, ValueError) as exc:
        progress("FAILED", str(exc))
        return 1
    except Exception as exc:
        # Do not leak request payloads or authentication details on unexpected exceptions.
        progress("FAILED", f"Unexpected exception: {type(exc).__name__}")
        return 1


if __name__ == "__main__":
    sys.exit(cli())
