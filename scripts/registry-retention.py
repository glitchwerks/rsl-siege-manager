#!/usr/bin/env python3
"""Bounded production registry cleanup. Stdlib only; never install extensions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

SUBSCRIPTION = "213aa1f8-32d1-4ffe-8f4d-6e60f1cd9dc0"
REGISTRY = "siegeacrprod"
REPOSITORIES = {"siege-api", "siege-bot", "siege-frontend"}
GROUPS = ("siege-web-prod", "siege-web-dev", "mom-bot")
REGISTRY_ID = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/siege-web-prod/providers/Microsoft.ContainerRegistry/registries/{REGISTRY}"
TASK_URL = f"https://management.azure.com{REGISTRY_ID}/tasks/weekly-purge?api-version=2019-06-01-preview"
DAYS = 7
KEEP = 5
MAX_DELETE = 100
MANIFEST_TYPES = {"application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"}


class Stop(RuntimeError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def azure(*args):
    result = subprocess.run(["az", *args, "--only-show-errors", "-o", "json"],
                            env=dict(os.environ, AZURE_EXTENSION_USE_DYNAMIC_INSTALL="no"),
                            capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise Stop("Azure request failed; raw CLI output suppressed")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def rest(url):
    return azure("rest", "--method", "get", "--url", url)


def listed(url):
    rows = []
    while url:
        if not url.startswith("https://management.azure.com/"):
            raise Stop("Unexpected pagination host")
        data = rest(url)
        rows.extend(data["value"])
        url = data.get("nextLink")
    return rows


def now():
    return datetime.now(timezone.utc)


def timestamp(value):
    # Azure timestamps may have seven fractional digits; fromisoformat truncates.
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(report, output, indent=2)
    os.replace(temporary, path)


def require_account():
    account = azure("account", "show")
    if account["id"] != SUBSCRIPTION or account["state"] != "Enabled":
        raise Stop("Wrong or disabled subscription")


def image_rows():
    repos = azure("acr", "repository", "list", "--name", REGISTRY)
    if set(repos) != REPOSITORIES:
        raise Stop("Repository scope changed; review before deletion")
    result = {}
    for repo in sorted(repos):
        rows = azure("acr", "repository", "show-manifests", "--name", REGISTRY,
                     "--repository", repo, "--detail")
        result[repo] = rows
    return result


def metadata(row):
    return {"digest": row["digest"], "tags": sorted(row.get("tags") or []),
            "updated": row["lastUpdateTime"], "attributes": row["changeableAttributes"]}


def inventory_hash(rows):
    return fingerprint(sorted([metadata(row) for row in rows], key=lambda row: row["digest"]))


def applications():
    apps = []
    refs = []
    for group in GROUPS:
        url = f"https://management.azure.com/subscriptions/{SUBSCRIPTION}/resourceGroups/{group}/providers/Microsoft.App/containerApps?api-version=2024-03-01"
        resources = listed(url)
        if not resources:
            raise Stop("Expected application group empty")
        for app in resources:
            props = app["properties"]
            if props.get("provisioningState") != "Succeeded":
                raise Stop("Deployment in progress or application failed")
            if props.get("runningStatus") not in (("Running", "Stopped") if group == "siege-web-dev" else ("Running",)):
                raise Stop("Application baseline unhealthy")
            containers = props.get("template", {}).get("containers", [])
            refs.extend(container["image"] for container in containers)
            ingress = props.get("configuration", {}).get("ingress") or {}
            apps.append({"id": app["id"], "status": props["runningStatus"],
                         "revision": props.get("latestReadyRevisionName"),
                         "images": sorted(container["image"] for container in containers),
                         "ingressHash": fingerprint(ingress)})
            revisions = listed(f"https://management.azure.com{app['id']}/revisions?api-version=2024-03-01")
            refs.extend(container["image"] for rev in revisions for container in rev["properties"].get("template", {}).get("containers", []))
    return {"apps": sorted(apps, key=lambda app: app["id"]), "references": sorted(refs)}


def health():
    # No actions, jobs, or Discord messages; existing public health endpoint only.
    for attempt in range(3):
        response = subprocess.run(["curl", "--ipv4", "--silent", "--show-error",
                                   "--connect-timeout", "5", "--max-time", "20",
                                   "--write-out", "\n%{http_code}", "https://rslsiege.com/api/health"],
                                  capture_output=True, text=True, timeout=25)
        if response.returncode:
            continue  # bounded transport retries; do not retry a negative health response
        try:
            body, code = response.stdout.rsplit("\n", 1)
            if code == "200" and json.loads(body).get("status") == "healthy":
                return {"web": "PASS", "restrictedBot": "CONTROL_PLANE_ONLY"}
        except (ValueError, KeyError):
            pass
        raise Stop("Public application health failed")
    raise Stop("Public health transport failed after bounded retries")


def usage():
    result = azure("acr", "show-usage", "--name", REGISTRY)
    return next(row["currentValue"] for row in result["value"] if row["name"] == "Size")


def plan(rows_by_repo, refs, at):
    cutoff = at - timedelta(days=DAYS)
    result = []
    for repo, rows in sorted(rows_by_repo.items()):
        if any(row.get("mediaType") not in MANIFEST_TYPES for row in rows):
            raise Stop("Nested manifest dependency analysis required")
        recent = sorted(rows, key=lambda row: timestamp(row["lastUpdateTime"]), reverse=True)
        keep = {row["digest"] for row in recent[:KEEP]}
        releases = [row for row in recent if any(tag.startswith("v") for tag in row.get("tags") or [])]
        keep.update(row["digest"] for row in releases[:KEEP])
        for row in recent:
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", row["digest"]):
                raise Stop("Unexpected manifest digest")
            identities = {f"{REGISTRY}.azurecr.io/{repo}@{row['digest']}"}
            identities.update(f"{REGISTRY}.azurecr.io/{repo}:{tag}" for tag in row.get("tags") or [])
            reasons = []
            if identities.intersection(refs):
                reasons.append("referenced by current template or retained revision")
            if row["digest"] in keep:
                reasons.append("latest five builds or latest five release manifests")
            if timestamp(row["lastUpdateTime"]) >= cutoff:
                reasons.append("within seven days")
            if not row["changeableAttributes"].get("deleteEnabled") or not row["changeableAttributes"].get("writeEnabled"):
                reasons.append("locked")
            result.append({"repository": repo, **metadata(row), "keepReasons": reasons,
                           "candidate": not reasons})
    if sum(row["candidate"] for row in result) > MAX_DELETE:
        raise Stop("Deletion budget exceeded; operator review required")
    return result


def preflight():
    refs = applications()
    rows = image_rows()
    at = now()
    result = {"schema": 1, "registry": REGISTRY, "subscription": SUBSCRIPTION,
              "days": DAYS, "keep": KEEP, "createdAt": at.isoformat(),
              "status": "PREFLIGHT_PASS", "baseline": refs, "health": health(),
              "images": plan(rows, refs["references"], at),
              "inventoryHashes": {repo: inventory_hash(values) for repo, values in rows.items()},
              "beforeBytes": usage(), "attempted": [], "deleted": [],
              "legacyTask": rest(TASK_URL)["properties"]["status"]}
    return result


def load(path):
    report = json.loads(Path(path).read_text())
    if (report.get("schema"), report.get("registry"), report.get("subscription"), report.get("days"), report.get("keep")) != (1, REGISTRY, SUBSCRIPTION, DAYS, KEEP):
        raise Stop("Report policy or scope mismatch")
    if not timedelta(0) <= now() - timestamp(report["createdAt"]) <= timedelta(minutes=20):
        raise Stop("Fresh preflight required")
    if report["status"] not in ("PREFLIGHT_PASS", "ACTIVATE_PASS"):
        raise Stop("Passed preflight required")
    return report


def unchanged(report):
    current = applications()
    if current != report["baseline"]:
        raise Stop("Application drift; stop before deletion")
    rows = image_rows()
    if {repo: inventory_hash(values) for repo, values in rows.items()} != report["inventoryHashes"]:
        raise Stop("Registry drift; stop before deletion")
    # Recompute at the original cutoff; report editing cannot expand the candidates.
    expected = plan(rows, current["references"], timestamp(report["createdAt"]))
    if expected != report["images"]:
        raise Stop("Candidate plan changed or report was modified")
    health()
    return rows


def activate(report, path):
    unchanged(report)
    task = rest(TASK_URL)
    if task["properties"]["status"] not in ("Enabled", "Disabled"):
        raise Stop("Unknown legacy task state")
    running = azure("acr", "task", "list-runs", "--registry", REGISTRY)
    if any(run.get("task") == "weekly-purge" and run.get("status") in ("Queued", "Started", "Running") for run in running):
        raise Stop("Legacy cleanup running; do not overlap")
    report["legacyBefore"] = task["properties"]["status"]
    report["legacyConfigHash"] = task_config(task)
    report["activationAttempted"] = True
    write(path, report)
    azure("rest", "--method", "patch", "--url", TASK_URL,
          "--body", json.dumps({"properties": {"status": "Disabled"}}))
    after = rest(TASK_URL)
    if after["properties"]["status"] != "Disabled" or task_config(after) != report["legacyConfigHash"]:
        raise Stop("Legacy task activation verification failed")
    unchanged(report)  # task-only update must not affect workloads or registry contents
    report["status"] = "ACTIVATE_PASS"
    write(path, report)


def task_config(task):
    props = dict(task["properties"])
    for key in ("status", "provisioningState", "creationDate"):
        props.pop(key, None)
    return fingerprint({"properties": props, "identity": task.get("identity"),
                        "tags": task.get("tags"), "location": task.get("location")})


def apply(report, path):
    if rest(TASK_URL)["properties"]["status"] != "Disabled":
        raise Stop("Activate replacement first; legacy task must be disabled")
    rows = unchanged(report)
    approved = [row for row in report["images"] if row["candidate"]]
    report["status"] = "APPLY_RUNNING"
    write(path, report)
    for repo in sorted(REPOSITORIES):
        if applications() != report["baseline"]:
            raise Stop("Application drift during cleanup; stop further deletion")
        current = azure("acr", "repository", "show-manifests", "--name", REGISTRY,
                        "--repository", repo, "--detail")
        if inventory_hash(current) != inventory_hash(rows[repo]):
            raise Stop("Repository drift; stop further deletion")
        for candidate in [row for row in approved if row["repository"] == repo]:
            image = f"{repo}@{candidate['digest']}"
            fresh = azure("acr", "repository", "show", "--name", REGISTRY, "--image", image)
            expected = {key: candidate[key] for key in metadata(fresh)}
            if metadata(fresh) != expected:
                raise Stop("Candidate metadata changed")
            entry = {"repository": repo, "digest": candidate["digest"]}
            report["attempted"].append(entry)
            write(path, report)  # unknown write outcomes remain recorded
            azure("acr", "repository", "delete", "--name", REGISTRY, "--image", image, "--yes")
            report["deleted"].append(entry)
            write(path, report)
            print(f"Deleted {len(report['deleted'])}/{len(approved)}", flush=True)
    verify(report, path)


def verify(report, path):
    rows = image_rows()
    deleted = {(row["repository"], row["digest"]) for row in report["deleted"]}
    for repo, values in rows.items():
        expected = sorted([{key: row[key] for key in ("digest", "tags", "updated", "attributes")}
                           for row in report["images"] if row["repository"] == repo and (repo, row["digest"]) not in deleted], key=lambda row: row["digest"])
        actual = sorted([metadata(row) for row in values], key=lambda row: row["digest"])
        if actual != expected:
            raise Stop("Retained images changed or deletion unverified")
    if applications() != report["baseline"]:
        raise Stop("Post-cleanup application drift")
    report["health"] = health()
    report["afterBytes"] = usage()
    report["remaining"] = {repo: len(values) for repo, values in rows.items()}
    report["status"] = "VERIFY_PASS"
    write(path, report)


def rollback_task(report, path):
    # This restores scheduling only; deleted historical images cannot be restored.
    if not report.get("activationAttempted") or report.get("legacyBefore") not in ("Enabled", "Disabled"):
        raise Stop("Activation baseline missing")
    task = rest(TASK_URL)
    if task_config(task) != report["legacyConfigHash"] or task["properties"]["status"] not in ("Enabled", "Disabled"):
        raise Stop("Concurrent task change; do not overwrite")
    azure("rest", "--method", "patch", "--url", TASK_URL,
          "--body", json.dumps({"properties": {"status": report["legacyBefore"]}}))
    if rest(TASK_URL)["properties"]["status"] != report["legacyBefore"]:
        raise Stop("Task rollback verification failed")
    report["status"] = "TASK_ROLLBACK_PASS"
    write(path, report)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("preflight", "activate", "apply", "verify", "rollback-task"))
    parser.add_argument("--report", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.phase in ("activate", "apply", "rollback-task") and not args.confirm:
        parser.error("State changes require --confirm")
    report = None
    try:
        require_account()
        if args.phase == "preflight":
            if Path(args.report).exists():
                raise Stop("Do not overwrite existing report")
            report = preflight()
            write(args.report, report)
        elif args.phase in ("verify", "rollback-task"):
            report = json.loads(Path(args.report).read_text())
            if (report.get("registry"), report.get("subscription"), report.get("schema")) != (REGISTRY, SUBSCRIPTION, 1):
                raise Stop("Report scope mismatch")
            if args.phase == "verify":
                if report["status"] not in ("APPLY_RUNNING", "VERIFY_PASS", "STOPPED_REVIEW_REQUIRED"):
                    raise Stop("No cleanup to verify")
                verify(report, args.report)
            else:
                rollback_task(report, args.report)
        else:
            report = load(args.report)
            if args.phase == "activate":
                activate(report, args.report)
            else:
                apply(report, args.report)
        print(report["status"], flush=True)
    except Exception as error:
        reason = str(error) if isinstance(error, Stop) else type(error).__name__
        if report is not None:
            report["status"] = "STOPPED_REVIEW_REQUIRED"
            report["reason"] = reason
            write(args.report, report)
        else:
            failure_path = str(args.report) + ".failure.json"
            write(failure_path, {"status": "STOPPED_REVIEW_REQUIRED", "phase": args.phase, "reason": reason})
        print("STOPPED_REVIEW_REQUIRED: " + reason, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
