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
import stat
from datetime import datetime, timezone
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PORTABLE = ROOT / "deploy" / "portable"


def read_secret_file(path):
    """Read a small private regular file; suppress paths, values, and OS errors."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                return None
            content = source.read(4097)
        if len(content) > 4096:
            return None
        # The PostgreSQL image reads this with shell command substitution,
        # which strips terminal LF characters but preserves other whitespace.
        return content.decode("utf-8").rstrip("\n")
    except (OSError, UnicodeError):
        return None


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
    def public_hostname():
        if not isinstance(host, str) or not 1 <= len(host) <= 253:
            return False
        labels = host.lower().split(".")
        reserved = ("example.com", "example.net", "example.org")
        return (len(labels) >= 2
                and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                        for label in labels)
                and not labels[-1].isdigit()
                and labels[-1] not in {"test", "example", "invalid", "localhost", "local", "internal"}
                and not any(host.lower() == name or host.lower().endswith("." + name)
                            for name in reserved))
    check("public_hostname", public_hostname)
    check("secure_auth_configuration", lambda: env["ENVIRONMENT"] == "production"
          and env["AUTH_DISABLED"] == "false"
          and "changeme" not in env.get("SESSION_SECRET", "").lower()
          and all(len(env.get(k, "").strip()) >= 32 and "REPLACE" not in env[k].upper()
                  for k in ("SESSION_SECRET", "DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN")))
    check("authentication_keys_distinct", lambda: len({env[k] for k in
          ("SESSION_SECRET", "DISCORD_BOT_API_KEY", "BOT_SERVICE_TOKEN")}) == 3)
    def configured_value(value):
        return isinstance(value, str) and bool(value.strip()) and "REPLACE" not in value.upper()

    check("oauth_public_origin", lambda: env["DISCORD_REDIRECT_URI"] == f"https://{host}/api/auth/callback"
          and env["ALLOWED_ORIGINS"] == f"https://{host}"
          and all(configured_value(env.get(k)) for k in ("DISCORD_CLIENT_ID", "DISCORD_CLIENT_SECRET")))
    check("private_sidecar_url", lambda: env["DISCORD_BOT_API_URL"] == f"http://{bot}:8001")

    check("role_sync_https_configuration", lambda:
          env.get("DAY_ROLE_SYNC_ENABLED", "false") in {"true", "false"}
          and (env.get("DAY_ROLE_SYNC_ENABLED", "false") == "false"
               or (topology == "mom" and env.get("DAY_ROLE_SYNC_URL") ==
                   f"https://{host}/api/internal/role-sync")))
    check("optional_day_role_ids_valid", lambda: all(
          k not in env or (isinstance(env[k], str) and re.fullmatch(r"[0-9]{17,20}", env[k])
                          and 0 < int(env[k]) < 2**64)
          for k in ("DISCORD_DAY_1_ROLE_ID", "DISCORD_DAY_2_ROLE_ID")))
    check("role_sync_receiver_route", lambda:
          proxy_env["ROLE_SYNC_UPSTREAM"] ==
              ("mom:8001" if topology == "mom" else "api-proxy:8000")
          and proxy_env["ROLE_SYNC_PATH"] == "/api/internal/role-sync"
          and "application" in services["proxy"]["networks"]
          and "application" in services[bot]["networks"])

    def proxy_trust():
        command = backend["command"]
        trusted = command[command.index("--forwarded-allow-ips") + 1]
        address = services["proxy"]["networks"]["proxy"]["ipv4_address"]
        subnet = ipaddress.ip_network(model["networks"]["proxy"]["ipam"]["config"][0]["subnet"])
        dynamic = ipaddress.ip_network(model["networks"]["proxy"]["ipam"]["config"][0]["ip_range"])
        ip = ipaddress.ip_address(address)
        return (trusted == address and ip in subnet and ip.is_private
                and ip not in (subnet.network_address, subnet.broadcast_address)
                and dynamic.subnet_of(subnet) and dynamic.num_addresses >= 4 and ip not in dynamic
                and "--proxy-headers" in command)
    check("trust_only_fixed_proxy", proxy_trust)
    check("backend_proxy_alias_unambiguous", lambda:
          services["backend"]["networks"]["proxy"]["aliases"] == ["api-proxy"]
          and not any("api-proxy" in (network or {}).get("aliases", [])
                      for name, service in services.items()
                      for network_name, network in service.get("networks", {}).items()
                      if name != "backend" or network_name != "proxy"))

    database_env = services.get("postgres", {}).get("environment", {})
    def administrator_secret():
        postgres = services["postgres"]
        mounts = postgres.get("secrets", [])
        if not any(mount.get("source") == "postgres-admin"
                   and mount.get("target", "postgres-admin") in
                   {"postgres-admin", "/run/secrets/postgres-admin"} for mount in mounts):
            return False
        if database_env.get("POSTGRES_PASSWORD_FILE") != "/run/secrets/postgres-admin":
            return False
        value = read_secret_file(model["secrets"]["postgres-admin"]["file"])
        return (configured_value(value) and len(value) >= 16
                and value not in (database_env["SIEGE_DB_PASSWORD"], database_env["MOM_DB_PASSWORD"]))
    check("postgres_admin_secret_valid", administrator_secret)
    # Both roles are initialized in either topology; one compromised password
    # must not authenticate as the other application's publicly known role.
    check("distinct_application_database_passwords", lambda:
          len(database_env["MOM_DB_PASSWORD"]) >= 16
          and "REPLACE" not in database_env["MOM_DB_PASSWORD"].upper()
          and database_env["SIEGE_DB_PASSWORD"] != database_env["MOM_DB_PASSWORD"])
    def database_url(service, variable, scheme, username, database, password_key):
        url = urlsplit(services[service]["environment"][variable])
        return (url.scheme == scheme and url.hostname == "postgres" and url.port == 5432
                and url.username == username and url.path == f"/{database}"
                and len(database_env.get(password_key, "")) >= 16
                and "REPLACE" not in database_env[password_key].upper()
                and unquote(url.password or "") == database_env[password_key])
    check("siege_database_credentials_match", lambda: database_url(
        "backend", "DATABASE_URL", "postgresql+asyncpg", "siege_app", "siege", "SIEGE_DB_PASSWORD"))
    check("siege_migration_matches_runtime", lambda: services["migrate-siege"]["environment"] == env)
    check("siege_entrypoint_does_not_auto_migrate", lambda: all(
        services[n].get("entrypoint") == [] for n in ("backend", "migrate-siege"))
        and services["migrate-siege"]["command"] == ["alembic", "upgrade", "head"])
    bot_env = services.get(bot, {}).get("environment", {})
    token_key = "MOM_BOT_SECRET_DISCORD_TOKEN" if topology == "mom" else "DISCORD_TOKEN"
    guild_key = "MOM_BOT_SECRET_GUILD_ID" if topology == "mom" else "DISCORD_GUILD_ID"
    check("discord_bot_identity_configured", lambda: configured_value(bot_env.get(token_key))
          and configured_value(bot_env.get(guild_key))
          and bot_env[guild_key].isascii() and bot_env[guild_key].isdigit()
          and int(bot_env[guild_key]) > 0 and env.get("DISCORD_GUILD_ID") == bot_env[guild_key])
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
        check("mom_migration_clears_entrypoint", lambda: services["migrate-mom"].get("entrypoint") == [])
    return checks


def private_runtime_files(topology, stack_env, project_directory):
    """Check modes without reading credentials or following final symlinks."""
    runtime = project_directory / "runtime"
    directories = {project_directory, runtime, stack_env.parent}
    files = {stack_env, runtime / "backend.env", runtime / "database.env",
             runtime / ("mom.env" if topology == "mom" else "bundled.env"),
             runtime / "postgres-admin"}
    try:
        for path in directories | files:
            metadata = path.lstat()
            expected = stat.S_ISDIR if path in directories else stat.S_ISREG
            if (not expected(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or metadata.st_mode & 0o077):
                return False
        return True
    except OSError:
        return False


def deployment_bind_sources_current(project_directory):
    """Require reviewed non-secret bind files, never container-created directories."""
    try:
        for name in ("Caddyfile", "init-databases.sql"):
            path = project_directory / name
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as source:
                metadata = os.fstat(source.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
                    return False
                if source.read(65537) != (PORTABLE / name).read_bytes():
                    return False
        return True
    except OSError:
        return False


def render(topology, stack_env, project_directory):
    command = ["docker", "compose", "--env-file", str(stack_env), "--project-directory",
               str(project_directory), "-f", str(PORTABLE / "compose.yml"), "-f",
               str(PORTABLE / f"compose.{topology}.yml"), "--profile", "maintenance",
               "config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode:
        raise RuntimeError("compose_render_failed")
    return json.loads(result.stdout)


def private_report_directory(path):
    try:
        metadata = path.lstat()
        return (stat.S_ISDIR(metadata.st_mode) and metadata.st_uid == os.geteuid()
                and not metadata.st_mode & 0o077)
    except OSError:
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", choices=["mom", "bundled"], required=True)
    parser.add_argument("--stack-env", type=Path, required=True)
    parser.add_argument("--project-directory", type=Path, default=PORTABLE)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        checks = {
            "runtime_credentials_private": private_runtime_files(
                args.topology, args.stack_env, args.project_directory),
            "deployment_bind_sources_current": deployment_bind_sources_current(args.project_directory),
        }
        if all(checks.values()):
            checks.update(validate(render(args.topology, args.stack_env, args.project_directory), args.topology))
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        # Compose stderr, exception messages, and rendered values may contain secrets.
        checks = {"compose_render": False}
    report = {"schema": 1, "phase": "configuration", "topology": args.topology,
              "timestamp": datetime.now(timezone.utc).isoformat(), "checks": checks,
              "result": "PASS" if checks and all(checks.values()) else "STOP"}
    # Do not overwrite earlier evidence or follow an existing report symlink.
    try:
        if not private_report_directory(args.report.parent):
            report["checks"]["report_directory_private"] = False
            raise OSError
        fd = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(report, output, indent=2)
            output.write("\n")
    except OSError:
        # Preserve existing/partial evidence. Never echo paths or OS diagnostics.
        report["checks"]["report_write"] = False
        report["result"] = "STOP"
        report["error"] = "report_write_failed"
        print(json.dumps(report))
        return 1
    print(json.dumps(report))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
