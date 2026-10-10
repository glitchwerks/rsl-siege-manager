#!/usr/bin/env python3
"""Render and validate portable Compose without contacting a container daemon.

Never print rendered configuration: it contains credentials. This is configuration
validation only, not host/network suitability, deployment, or pilot acceptance.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "deploy" / "portable"


def validate(model, topology):
    checks = {}
    services = model.get("services", {})

    def check(name, predicate):
        try:
            checks[name] = bool(predicate())
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            checks[name] = False

    bot = "mom" if topology == "mom" else "bot"
    expected = {"postgres", "backend", "frontend", "proxy", "migrate-siege", bot}
    if topology == "mom":
        expected.add("migrate-mom")
    check("exactly_one_topology", lambda: set(services) == expected)
    for name, service in services.items():
        # Fixed check labels only; do not echo untrusted service names or values.
        if name not in expected:
            continue
        check(f"{name}_immutable_image", lambda: re.fullmatch(
            r"[^\s@]+@sha256:[0-9a-f]{64}", service.get("image", "")))
        check(f"{name}_no_build_or_host_access", lambda: not any(
            service.get(k) for k in ("build", "privileged", "network_mode", "pid", "devices"))
            and not any("docker.sock" in str(v) or "podman.sock" in str(v)
                        for v in service.get("volumes", [])))
        check(f"{name}_bounded_resources", lambda: int(service.get("mem_limit", 0)) > 0
              and float(service.get("cpus", 0)) > 0 and int(service.get("pids_limit", 0)) > 0)
        check(f"{name}_bounded_logs", lambda: service["logging"]["driver"] == "json-file"
              and service["logging"]["options"] == {"max-file": "3", "max-size": "10m"})
        if name != "proxy":
            check(f"{name}_private_ports", lambda: not service.get("ports"))
    check("only_https_proxy_published", lambda: sorted(
        (int(p["published"]), int(p["target"]), p.get("protocol", "tcp"))
        for p in services["proxy"]["ports"]
    ) == [(80, 80, "tcp"), (443, 443, "tcp")])
    check("database_internal_network", lambda: model["networks"]["database"]["internal"] is True)
    check("database_only_on_private_network", lambda: set(services["postgres"]["networks"]) == {"database"})
    check("maintenance_is_opt_in", lambda: all(
        services[n].get("profiles") == ["maintenance"] and services[n]["restart"] == "no"
        for n in expected if n.startswith("migrate-")))

    backend = services.get("backend", {})
    env = backend.get("environment", {})
    proxy_env = services.get("proxy", {}).get("environment", {})
    host = proxy_env.get("PUBLIC_HOST", "")
    check("public_hostname", lambda: bool(re.fullmatch(
        r"(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", host))
        and "." in host)
    check("secure_auth_configuration", lambda: env["ENVIRONMENT"] == "production"
          and env["AUTH_DISABLED"] == "false"
          and all(len(env.get(k, "")) >= 32 and "REPLACE" not in env[k]
                  for k in ("SESSION_SECRET", "DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN")))
    check("oauth_public_origin", lambda: env["DISCORD_REDIRECT_URI"] == f"https://{host}/api/auth/callback"
          and env["ALLOWED_ORIGINS"] == f"https://{host}"
          and bool(env.get("DISCORD_CLIENT_ID")) and bool(env.get("DISCORD_CLIENT_SECRET")))
    check("private_sidecar_url", lambda: env["DISCORD_BOT_API_URL"] == f"http://{bot}:8001")

    def proxy_trust():
        command = backend["command"]
        trusted = command[command.index("--forwarded-allow-ips") + 1]
        address = services["proxy"]["networks"]["proxy"]["ipv4_address"]
        subnet = ipaddress.ip_network(model["networks"]["proxy"]["ipam"]["config"][0]["subnet"])
        ip = ipaddress.ip_address(address)
        return (trusted == address and ip in subnet and ip.is_private
                and ip not in (subnet.network_address, subnet.broadcast_address)
                and "--proxy-headers" in command)
    check("trust_only_fixed_proxy", proxy_trust)

    database_env = services.get("postgres", {}).get("environment", {})
    def database_url(service, variable, scheme, username, database, password_key):
        url = urlsplit(services[service]["environment"][variable])
        return (url.scheme == scheme and url.hostname == "postgres" and url.port == 5432
                and url.username == username and url.path == f"/{database}"
                and len(database_env.get(password_key, "")) >= 16
                and "REPLACE" not in database_env[password_key]
                and unquote(url.password or "") == database_env[password_key])
    check("siege_database_credentials_match", lambda: database_url(
        "backend", "DATABASE_URL", "postgresql+asyncpg", "siege_app", "siege", "SIEGE_DB_PASSWORD"))
    check("siege_migration_matches_runtime", lambda: services["migrate-siege"]["environment"] == env)
    bot_env = services.get(bot, {}).get("environment", {})
    check("sidecar_auth_matches", lambda: env["DISCORD_BOT_API_KEY"] == bot_env[
        "MOM_BOT_SECRET_DISCORD_BOT_API_KEY" if topology == "mom" else "BOT_API_KEY"])
    if topology == "mom":
        check("mom_database_credentials_match", lambda: database_url(
            "mom", "MOM_BOT_DATABASE_URL", "postgresql+psycopg", "mom_app", "mom_bot", "MOM_DB_PASSWORD"))
        check("mom_portable_auth_explicit", lambda: bot_env["MOM_BOT_SECRET_SOURCE"] == "environment"
              and bot_env["MOM_BOT_DATABASE_AUTH"] == "password" and bot_env["MOM_BOT_ENV"] == "prod")
        check("mom_reverse_call_matches", lambda: bot_env["MOM_BOT_SECRET_SIEGE_WEB_URL"] == "http://backend:8000"
              and bot_env["MOM_BOT_SECRET_SIEGE_WEB_BOT_TOKEN"] == env["BOT_SERVICE_TOKEN"])
        check("mom_migration_matches_runtime", lambda: services["migrate-mom"]["environment"] == bot_env
              and services["migrate-mom"]["command"] == ["/app/.venv/bin/alembic", "upgrade", "head"])
    return checks


def render(topology, stack_env, project_directory):
    command = ["docker", "compose", "--env-file", str(stack_env), "--project-directory",
               str(project_directory), "-f", str(PORTABLE / "compose.yml"), "-f",
               str(PORTABLE / f"compose.{topology}.yml"), "--profile", "maintenance",
               "config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("compose_render_failed")
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", choices=["mom", "bundled"], required=True)
    parser.add_argument("--stack-env", type=Path, required=True)
    parser.add_argument("--project-directory", type=Path, default=PORTABLE)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        checks = validate(render(args.topology, args.stack_env, args.project_directory), args.topology)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        # Compose stderr, exception messages, and rendered values may contain secrets.
        checks = {"compose_render": False}
    report = {"schema": 1, "phase": "configuration", "topology": args.topology,
              "timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "result": "PASS" if checks and all(checks.values()) else "STOP"}
    # Do not overwrite earlier evidence or follow an existing report symlink.
    fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(report, output, indent=2)
        output.write("\n")
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
