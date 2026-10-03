#!/usr/bin/env python3
"""Apply a bounded Azure cost change using separately gated phases.

No packages/extensions are installed. Reports contain configuration metadata,
hashes, and check outcomes only; CLI output and workload logs are never saved.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from datetime import datetime, timezone

ALERT_API = "2023-12-01"
REGISTRY_API = "2023-07-01"
APP_API = "2024-03-01"


class CheckFailed(RuntimeError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def azure(*args):
    env = dict(os.environ, AZURE_EXTENSION_USE_DYNAMIC_INSTALL="no")
    result = subprocess.run(
        ["az", *args, "--only-show-errors", "-o", "json"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    if result.returncode:
        raise CheckFailed("Azure request failed; no CLI output retained")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def url(resource_id, version):
    return f"https://management.azure.com{resource_id}?api-version={version}"


def read(target):
    return azure("rest", "--method", "get", "--url", url(target["id"], target["api"]))


def current_value(target, resource):
    if target["kind"] == "registry":
        return resource["sku"]["name"]
    return resource["properties"]["evaluationFrequency"]


def invariant(target, resource):
    """Hash configuration we are not authorized to change."""
    if target["kind"] == "registry":
        return digest({k: resource.get(k) for k in ("id", "location", "tags", "properties")})
    properties = dict(resource["properties"])
    properties.pop("evaluationFrequency", None)
    # Azure may update these service-generated fields after a PATCH.
    for key in ("provisioningState", "lastUpdatedTime", "createdWithApiVersion"):
        properties.pop(key, None)
    return digest({"location": resource["location"], "tags": resource.get("tags"),
                   "properties": properties})


def patch(target, value):
    body = ({"sku": {"name": value}} if target["kind"] == "registry"
            else {"properties": {"evaluationFrequency": value}})
    azure("rest", "--method", "patch", "--url", url(target["id"], target["api"]),
          "--body", json.dumps(body))


def targets(subscription):
    root = f"/subscriptions/{subscription}/resourceGroups"
    result = [{"id": f"{root}/siege-web-prod/providers/Microsoft.ContainerRegistry/registries/siegeacrprod",
               "kind": "registry", "api": REGISTRY_API, "desired": "Basic"}]
    for environment in ("dev", "prod"):
        for name in ("latency-p95", "image-gen-slow"):
            result.append({"id": f"{root}/siege-web-{environment}/providers/Microsoft.Insights/scheduledQueryRules/siege-web-alert-{name}-{environment}",
                           "kind": "alert", "api": ALERT_API, "desired": "PT5M"})
    return result


def service_baseline(subscription):
    """Only metadata: no environment variables, secrets, or workload content."""
    root = f"/subscriptions/{subscription}/resourceGroups"
    apps = []
    health = []
    critical = []
    for group in ("siege-web-dev", "siege-web-prod", "mom-bot"):
        data = azure("rest", "--method", "get", "--url",
                     url(f"{root}/{group}/providers/Microsoft.App/containerApps", APP_API))
        for resource in data["value"]:
            p = resource["properties"]
            if p.get("provisioningState") != "Succeeded" or p.get("runningStatus") not in ("Running", "Stopped"):
                raise CheckFailed("Application baseline is not healthy")
            if group != "siege-web-dev" and p.get("runningStatus") != "Running":
                raise CheckFailed("Production application is not running")
            apps.append({"id": resource["id"], "runningStatus": p.get("runningStatus"),
                         "revision": p.get("latestReadyRevisionName"),
                         "ingress_hash": digest(p.get("configuration", {}).get("ingress")),
                         "images": [c["image"] for c in p.get("template", {}).get("containers", [])]})
            ingress = p.get("configuration", {}).get("ingress") or {}
            restricted_bot = group == "mom-bot" and any(
                rule.get("action") == "Allow" for rule in ingress.get("ipSecurityRestrictions") or []
            )
            if restricted_bot:
                health.append({"id": resource["id"], "status": "CONTROL_PLANE_ONLY_IP_RESTRICTED"})
            if group != "siege-web-dev" and not restricted_bot and ingress.get("external") and ingress.get("fqdn"):
                # Existing unauthenticated health endpoint only; do not exercise
                # member actions, create jobs, or emit Discord notifications.
                domains = ingress.get("customDomains") or []
                hostname = domains[0]["name"] if domains else ingress["fqdn"]
                try:
                    # Use the installed curl client, as for the operator's smoke
                    # check. Some public proxies reject Python's default client.
                    response = subprocess.run(
                        ["curl", "--silent", "--show-error", "--max-time", "20",
                         "--write-out", "\n%{http_code}", f"https://{hostname}/api/health"],
                        capture_output=True, text=True, timeout=25,
                    )
                    if response.returncode:
                        raise CheckFailed("Health request transport failed")
                    body, status = response.stdout.rsplit("\n", 1)
                    if status != "200":
                        raise CheckFailed(f"Health request returned HTTP {status}")
                    payload = json.loads(body)
                    if payload.get("status") != "healthy":
                        raise CheckFailed("Application health endpoint failed")
                    if "bot_connected" in payload and payload["bot_connected"] is not True:
                        raise CheckFailed("Discord gateway is disconnected")
                    health.append({"id": resource["id"], "status": "PASS"})
                except Exception as error:
                    reason = str(error) if isinstance(error, CheckFailed) else type(error).__name__
                    raise CheckFailed(f"Public health check failed: {resource['name']} ({reason})") from None
        data = azure("rest", "--method", "get", "--url",
                     url(f"{root}/{group}/providers/Microsoft.Insights/scheduledQueryRules", ALERT_API))
        selected = {t["id"].lower() for t in targets(subscription)}
        for resource in data["value"]:
            if resource["id"].lower() not in selected:
                critical.append({"id": resource["id"], "hash": digest(resource["properties"])})
    return {"apps": sorted(apps, key=lambda a: a["id"]),
            "health": sorted(health, key=lambda a: a["id"]),
            "other_alerts": sorted(critical, key=lambda a: a["id"])}


def registry_baseline():
    repositories = azure("acr", "repository", "list", "--name", "siegeacrprod")
    inventory = {}
    for repository in sorted(repositories):
        tags = azure("acr", "repository", "show-tags", "--name", "siegeacrprod",
                     "--repository", repository)
        inventory[repository] = digest(sorted(tags))
    usage = azure("acr", "show-usage", "--name", "siegeacrprod")
    size = next(item["currentValue"] for item in usage["value"] if item["name"] == "Size")
    return {"tag_hashes": inventory, "storage_bytes": size}


def preflight(subscription):
    result = {"schema": 1, "subscription": subscription,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "baseline": service_baseline(subscription), "registry": registry_baseline(),
              "targets": [], "attempted": [],
              "status": "PREFLIGHT_PASS"}
    for target in targets(subscription):
        resource = read(target)
        old = current_value(target, resource)
        if target["kind"] == "registry" and old not in ("Basic", "Standard"):
            raise CheckFailed("Unexpected registry tier; review dependencies")
        if target["kind"] == "alert":
            p = resource["properties"]
            if old not in ("PT1M", "PT5M") or not p.get("enabled") or p["windowSize"] != "PT5M":
                raise CheckFailed("Unexpected advisory alert configuration")
            if any("ago(5m)" not in rule.get("query", "") for rule in p["criteria"]["allOf"]):
                raise CheckFailed("Advisory query window needs review")
        result["targets"].append(dict(target, before=old, invariant=invariant(target, resource)))
    return result


def check_target(target, expected):
    # A registry SKU operation may take time; cap polling at 60 seconds.
    for attempt in range(13):
        resource = read(target)
        if current_value(target, resource) == expected:
            if invariant(target, resource) != target["invariant"]:
                raise CheckFailed("Unrelated target configuration changed")
            if resource.get("properties", {}).get("provisioningState", "Succeeded") == "Succeeded":
                return
        if attempt < 12:
            time.sleep(5)
    raise CheckFailed("Target verification failed")


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def rollback(report, persist):
    failures = []
    for index in reversed(report["attempted"]):
        target = report["targets"][index]
        try:
            resource = read(target)
            if invariant(target, resource) != target["invariant"]:
                raise CheckFailed("Concurrent target configuration change")
            value = current_value(target, resource)
            if value not in (target["before"], target["desired"]):
                raise CheckFailed("Concurrent target value change")
            if value != target["before"]:
                patch(target, target["before"])
            check_target(target, target["before"])
        except Exception:
            failures.append(target["id"])
    report["status"] = "ROLLBACK_FAILED" if failures else "ROLLBACK_PASS"
    report["rollback_failures"] = failures
    persist()
    if failures:
        raise CheckFailed("Rollback incomplete; see preserved report")


def apply(report, persist):
    created = datetime.fromisoformat(report["created_at"])
    if (datetime.now(timezone.utc) - created).total_seconds() > 3600:
        raise CheckFailed("Preflight older than one hour; create a fresh report")
    if report["status"] != "PREFLIGHT_PASS" or report["attempted"]:
        raise CheckFailed("Apply requires a fresh preflight report")
    if service_baseline(report["subscription"]) != report["baseline"]:
        raise CheckFailed("Service state changed since preflight")
    if registry_baseline()["tag_hashes"] != report["registry"]["tag_hashes"]:
        raise CheckFailed("Registry images changed since preflight")
    for target in report["targets"]:
        check_target(target, target["before"])
    try:
        report["status"] = "APPLY_RUNNING"
        persist()
        for index, target in enumerate(report["targets"]):
            if target["before"] != target["desired"]:
                # Save before requesting mutation, including uncertain network outcomes.
                report["attempted"].append(index)
                persist()
                patch(target, target["desired"])
            check_target(target, target["desired"])
        if service_baseline(report["subscription"]) != report["baseline"]:
            raise CheckFailed("Unrelated services changed during apply")
        if registry_baseline()["tag_hashes"] != report["registry"]["tag_hashes"]:
            raise CheckFailed("Registry images changed during apply")
        report["status"] = "APPLY_PASS"
        persist()
    except Exception:
        rollback(report, persist)
        raise CheckFailed("Apply failed; rollback attempted; see preserved report") from None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("preflight", "apply", "verify", "rollback"))
    parser.add_argument("--subscription", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    report = None
    try:
        if args.phase in ("apply", "rollback") and not args.confirm:
            raise CheckFailed("Mutation requires explicit --confirm after report review")
        account = azure("account", "show")
        if account["id"].lower() != args.subscription.lower() or account["state"] != "Enabled":
            raise CheckFailed("Active Azure subscription does not match the requested target")
        if args.phase == "preflight":
            if args.report.exists():
                raise CheckFailed("Use a new report path; never overwrite recovery evidence")
            report = preflight(args.subscription)
            save(args.report, report)
        else:
            report = json.loads(args.report.read_text())
            expected = targets(args.subscription)
            actual = [{k: t[k] for k in ("id", "kind", "api", "desired")} for t in report["targets"]]
            if report["subscription"] != args.subscription or actual != expected or report.get("schema") != 1:
                raise CheckFailed("Report does not match the fixed target allowlist")
            persist = lambda: save(args.report, report)
            if args.phase == "apply":
                apply(report, persist)
            elif args.phase == "rollback":
                rollback(report, persist)
            else:
                expected_key = "before" if report["status"] == "ROLLBACK_PASS" else "desired"
                for target in report["targets"]:
                    check_target(target, target[expected_key])
                if service_baseline(args.subscription) != report["baseline"]:
                    raise CheckFailed("Unrelated service baseline changed")
                if registry_baseline()["tag_hashes"] != report["registry"]["tag_hashes"]:
                    raise CheckFailed("Registry images changed")
                report["verification"] = "PASS"
                persist()
        print(f"PASS {args.phase}: {args.report}")
        return 0
    except Exception as error:
        # Never echo Azure responses, URLs containing credentials, or workload logs.
        message = str(error) if isinstance(error, CheckFailed) else type(error).__name__
        failure_path = args.report.with_suffix(".failure.json")
        save(failure_path, {"phase": args.phase, "status": "FAIL", "reason": message})
        print(f"FAIL {args.phase}: {message}; report: {failure_path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
