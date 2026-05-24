"""In-memory fake Discord client for integration testing.

Role-sync magic trigger values (``apply_day_role``)
-----------------------------------------------------
The following role snowflake IDs produce exception paths in the fake client:

  ``apply_day_role``:
    - role_id ``"role-forbidden-id"``   → raises ``discord.Forbidden``
    - role_id ``"role-http4xx-id"``     → raises ``discord.HTTPException`` 429
    - role_id ``"role-http5xx-id"``     → raises ``discord.HTTPException`` 500

Known good role snowflake: ``KNOWN_ROLE_ID = "888000888000888001"``
Unknown role snowflake  : any value not in the known set → ``get_role`` returns ``None``

# noqa: E501 — the original docstring continuation below

Used when ``BOT_TEST_MODE=fake`` is set.  Replaces the real ``SiegeBot``
(discord.py) with a deterministic, zero-network implementation that the
HTTP sidecar can call through its normal seams.

A second mode ``BOT_TEST_MODE=fake_broken_shape`` activates the same client
but overrides ``get_members()`` to return intentionally-wrong element shapes
(Option A breakage).  The HTTP handlers in ``http_api.py`` also check this
mode to inject broken envelopes for endpoints whose response is built in the
handler itself (Option B breakage, applied to ``/api/health`` and
``POST /api/notify``).  This supports the engineered-break meta-tests in
``backend/tests/integration/sidecar/test_meta_shape_assertions.py``.

Design goals:
- No real Discord token or network connection required.
- All seam methods (``send_dm``, ``post_message``, ``post_image``,
  ``get_members``) behave deterministically based on pre-configured state.
- Exception injection: callers can trigger 403/502/503 paths by passing
  magic usernames / channel names to the respective endpoints.
- ``is_ready()`` returns ``True`` so ``/api/health`` reports
  ``bot_connected: true``.
- ``get_guild()`` returns a fake guild object that supports
  ``fetch_member()`` for the ``GET /api/members/{id}`` endpoint.

Magic trigger values
--------------------
The following usernames / channel names produce exception-translation paths:

  ``send_dm``:
    - username ``"dm-forbidden"``    → raises ``discord.Forbidden`` (403 path)
    - username ``"dm-http4xx"``      → raises ``discord.HTTPException`` status 429
    - username ``"dm-http5xx"``      → raises ``discord.HTTPException`` status 500
    - username ``"dm-timeout"``      → raises ``asyncio.TimeoutError``

  ``post_message`` / ``post_image``:
    - channel ``"chan-forbidden"``   → raises ``discord.Forbidden`` (403 path)
    - channel ``"chan-http4xx"``     → raises ``discord.HTTPException`` status 429
    - channel ``"chan-http5xx"``     → raises ``discord.HTTPException`` status 500
    - channel ``"chan-timeout"``     → raises ``asyncio.TimeoutError``

Known good members and channels
---------------------------------
  Member  username=``"known-user"``, id=``"111000111000111001"``
  Channel name=``"known-channel"``

These are the only fixtures that produce 200 success responses.
Any other username / channel that is not a magic trigger raises
``ValueError`` (→ 404).
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock

import discord

# ---------------------------------------------------------------------------
# Test-mode helpers
# ---------------------------------------------------------------------------


def is_broken_shape_mode() -> bool:
    """Return ``True`` when running in ``BOT_TEST_MODE=fake_broken_shape``.

    Reads the environment variable at call time (not at module-import time)
    so that in-process tests can toggle the mode via ``monkeypatch.setenv``
    or ``os.environ`` assignment without needing a subprocess restart.

    Used by ``FakeDiscordClient.get_members()`` (Option A breakage) and by
    the HTTP handlers in ``http_api.py`` (Option B breakage) to inject
    intentionally-wrong response shapes for the engineered-break meta-tests.

    Returns:
        ``True`` if ``BOT_TEST_MODE`` is ``"fake_broken_shape"``,
        ``False`` for all other values including ``"fake"``.
    """
    return os.environ.get("BOT_TEST_MODE", "").lower() == "fake_broken_shape"


# ---------------------------------------------------------------------------
# Constants — known fixtures that integration tests build on
# ---------------------------------------------------------------------------

KNOWN_MEMBER_ID = "111000111000111001"
KNOWN_MEMBER_USERNAME = "known-user"
KNOWN_MEMBER_DISPLAY_NAME = "Known User"
KNOWN_CHANNEL_NAME = "known-channel"
KNOWN_IMAGE_URL = "https://cdn.discordapp.com/attachments/fake/board.png"

# Role fixture for apply_day_role tests
KNOWN_ROLE_ID = "888000888000888001"
KNOWN_ROLE_NAME = "Day 1"

# Snowflake used for the guild itself (any non-zero value works)
FAKE_GUILD_ID = 123456789


def _make_discord_http_exc(http_status: int, text: str = "") -> discord.HTTPException:
    """Build a ``discord.HTTPException`` with the given HTTP status code.

    Uses ``MagicMock`` for the response object so we never need a real
    ``aiohttp.ClientResponse``.

    Args:
        http_status: Integer HTTP status code to attach to the exception.
        text: Optional response body text (maps to ``exc.text``).

    Returns:
        A ``discord.HTTPException`` instance with ``.status`` set.
    """
    response = MagicMock()
    response.status = http_status
    response.reason = "test"
    return discord.HTTPException(response, text)


# ---------------------------------------------------------------------------
# Fake guild returned by ``get_guild()``
# ---------------------------------------------------------------------------


class _FakeRole:
    """Minimal stand-in for ``discord.Role``."""

    def __init__(self, role_id: str, name: str) -> None:
        """Initialise the fake role.

        Args:
            role_id: Discord snowflake string for this role.
            name: Human-readable role name (e.g. ``"Day 1"``).
        """
        self.id = int(role_id)
        self.name = name


class _FakeMember:
    """Minimal stand-in for ``discord.Member``."""

    def __init__(
        self,
        member_id: str,
        username: str,
        display_name: str,
    ) -> None:
        self.id = int(member_id)
        self.name = username
        self.display_name = display_name
        # Provide an empty roles list (no @everyone mock needed for fake).
        self.roles: list[Any] = []

    async def add_roles(self, role: _FakeRole) -> None:
        """Append ``role`` to this member's role list.

        Args:
            role: The ``_FakeRole`` instance to add.
        """
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role: _FakeRole) -> None:
        """Remove ``role`` from this member's role list if present.

        Args:
            role: The ``_FakeRole`` instance to remove.
        """
        self.roles = [r for r in self.roles if r is not role]


class _FakeGuild:
    """Minimal stand-in for ``discord.Guild``.

    Supports ``fetch_member()`` for the ``GET /api/members/{id}`` path
    and ``get_role()`` for the ``POST /api/role-sync`` path.

    The known member is returned for ``KNOWN_MEMBER_ID``; any other ID
    raises ``discord.NotFound``.  The known role is returned for
    ``KNOWN_ROLE_ID``; any other ID returns ``None``.
    """

    def __init__(self) -> None:
        self._known_member = _FakeMember(
            KNOWN_MEMBER_ID,
            KNOWN_MEMBER_USERNAME,
            KNOWN_MEMBER_DISPLAY_NAME,
        )
        self._known_role = _FakeRole(KNOWN_ROLE_ID, KNOWN_ROLE_NAME)

    async def fetch_member(self, user_id: int) -> _FakeMember:
        """Simulate ``Guild.fetch_member()``.

        Args:
            user_id: Integer Discord snowflake to look up.

        Returns:
            The fake ``_FakeMember`` when the ID matches ``KNOWN_MEMBER_ID``.

        Raises:
            discord.NotFound: When the ID does not match the known member.
        """
        if user_id == int(KNOWN_MEMBER_ID):
            return self._known_member
        response = MagicMock()
        response.status = 404
        response.reason = "Not Found"
        raise discord.NotFound(response, "Unknown Member")

    def get_role(self, role_id: int) -> _FakeRole | None:
        """Simulate ``Guild.get_role()``.

        Args:
            role_id: Integer Discord snowflake of the role to look up.

        Returns:
            The fake ``_FakeRole`` when the ID matches ``KNOWN_ROLE_ID``,
            ``None`` otherwise (role does not exist in guild).
        """
        if role_id == int(KNOWN_ROLE_ID):
            return self._known_role
        return None


# ---------------------------------------------------------------------------
# Fake Discord client
# ---------------------------------------------------------------------------


class FakeDiscordClient:
    """In-memory fake that satisfies the seams consumed by ``http_api.py``.

    Instantiated by ``bot/app/main.py`` when ``BOT_TEST_MODE=fake``.

    Implements the same public seam as ``SiegeBot`` (duck-typed), plus
    ``get_guild()`` which ``get_guild_member`` calls directly on ``_bot``.
    """

    def __init__(self, guild_id: int) -> None:
        """Initialise the fake client.

        Args:
            guild_id: The Discord guild ID (read from ``DISCORD_GUILD_ID``
                env var and forwarded from ``main.py``).  Stored so that
                ``get_guild(guild_id)`` returns the fake guild.
        """
        self._guild_id = guild_id
        self._fake_guild = _FakeGuild()

    # ------------------------------------------------------------------
    # Lifecycle / readiness
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """Report the fake client as always ready.

        Returns:
            Always ``True`` so ``/api/health`` returns
            ``bot_connected: true``.
        """
        return True

    def get_guild(self, guild_id: int) -> _FakeGuild | None:
        """Return the fake guild when the ID matches; ``None`` otherwise.

        Args:
            guild_id: The guild ID to look up.

        Returns:
            The ``_FakeGuild`` instance when ``guild_id`` matches the
            configured ID, ``None`` otherwise.
        """
        if guild_id == self._guild_id:
            return self._fake_guild
        return None

    # ------------------------------------------------------------------
    # Seam methods (mirror ``SiegeBot``)
    # ------------------------------------------------------------------

    async def send_dm(self, username: str, message: str) -> None:
        """Simulate sending a DM.

        Args:
            username: Target member's Discord username.  Magic values
                trigger specific exceptions (see module docstring).
            message: Message content (ignored in the fake).

        Raises:
            discord.Forbidden: When ``username == "dm-forbidden"``.
            discord.HTTPException: When username is ``"dm-http4xx"``
                (status 429) or ``"dm-http5xx"`` (status 500).
            asyncio.TimeoutError: When ``username == "dm-timeout"``.
            ValueError: When the username is not ``KNOWN_MEMBER_USERNAME``
                and not a magic trigger.
        """
        if username == "dm-forbidden":
            response = MagicMock()
            response.status = 403
            response.reason = "Forbidden"
            raise discord.Forbidden(response, "Cannot send messages to this user")
        if username == "dm-http4xx":
            raise _make_discord_http_exc(429, "You are being rate limited")
        if username == "dm-http5xx":
            raise _make_discord_http_exc(500, "Internal Server Error")
        if username == "dm-timeout":
            raise TimeoutError()
        if username.lower() != KNOWN_MEMBER_USERNAME.lower():
            raise ValueError(f"Member '{username}' not found in guild")

    async def post_message(self, channel_name: str, message: str) -> None:
        """Simulate posting a message to a channel.

        Args:
            channel_name: Target channel name.  Magic values trigger
                specific exceptions (see module docstring).
            message: Message content (ignored in the fake).

        Raises:
            discord.Forbidden: When ``channel_name == "chan-forbidden"``.
            discord.HTTPException: When channel_name is ``"chan-http4xx"``
                (status 429) or ``"chan-http5xx"`` (status 500).
            asyncio.TimeoutError: When ``channel_name == "chan-timeout"``.
            ValueError: When the channel is not ``KNOWN_CHANNEL_NAME``
                and not a magic trigger.
        """
        self._check_channel_exceptions(channel_name)
        if channel_name != KNOWN_CHANNEL_NAME:
            raise ValueError(f"Channel '{channel_name}' not found in guild")

    async def post_image(
        self,
        channel_name: str,
        image_bytes: bytes,
        filename: str = "image.png",
    ) -> str:
        """Simulate posting an image to a channel.

        Args:
            channel_name: Target channel name.  Magic values trigger
                specific exceptions (see module docstring).
            image_bytes: Image bytes (ignored in the fake).
            filename: Attachment filename (ignored in the fake).

        Returns:
            ``KNOWN_IMAGE_URL`` on success.

        Raises:
            discord.Forbidden: When ``channel_name == "chan-forbidden"``.
            discord.HTTPException: When channel_name is ``"chan-http4xx"``
                (status 429) or ``"chan-http5xx"`` (status 500).
            asyncio.TimeoutError: When ``channel_name == "chan-timeout"``.
            ValueError: When the channel is not ``KNOWN_CHANNEL_NAME``
                and not a magic trigger.
        """
        self._check_channel_exceptions(channel_name)
        if channel_name != KNOWN_CHANNEL_NAME:
            raise ValueError(f"Channel '{channel_name}' not found in guild")
        return KNOWN_IMAGE_URL

    async def apply_day_role(
        self,
        discord_id: str,
        role_id: str,
        action: str,
    ) -> str:
        """Simulate toggling a day role for a guild member.

        Args:
            discord_id: Discord snowflake string of the target member.
                Pass any value other than ``KNOWN_MEMBER_ID`` to trigger
                ``discord.NotFound`` (member-not-found path).
            role_id: Discord snowflake string of the role to toggle.
                Pass ``KNOWN_ROLE_ID`` for the happy path.  Any unknown
                ID returns ``None`` from ``get_role`` → ``ValueError``.
            action: ``"assign"`` or ``"unassign"``.

        Returns:
            ``"applied"`` on success.

        Raises:
            discord.NotFound: When ``discord_id != KNOWN_MEMBER_ID``.
            ValueError: When ``role_id`` is not in the fake guild
                (simulates role-not-found path).
        """
        member = await self._fake_guild.fetch_member(int(discord_id))
        role = self._fake_guild.get_role(int(role_id))
        if role is None:
            raise ValueError(f"Role '{role_id}' not found in guild")
        if action == "assign":
            await member.add_roles(role)
        else:
            await member.remove_roles(role)
        return "applied"

    async def get_members(self) -> list[dict]:
        """Return a one-element member list for the known member.

        In ``fake_broken_shape`` mode (Option A breakage) each element
        contains only ``id`` — ``username`` and ``display_name`` are
        intentionally omitted.  This propagates the broken shape to the
        wire via ``GET /api/members`` so the engineered-break meta-tests
        can confirm that the regular shape assertion would catch this.

        Returns:
            In normal fake mode: a list with a single dict containing
            ``id``, ``username``, and ``display_name``.

            In broken-shape mode: a list with a single dict containing
            only ``id`` (missing the two required keys).
        """
        if is_broken_shape_mode():
            # Option A: return only ``id``; drop ``username`` and
            # ``display_name`` so the existing shape assertion fails.
            return [{"id": KNOWN_MEMBER_ID}]
        return [
            {
                "id": KNOWN_MEMBER_ID,
                "username": KNOWN_MEMBER_USERNAME,
                "display_name": KNOWN_MEMBER_DISPLAY_NAME,
            }
        ]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_channel_exceptions(self, channel_name: str) -> None:
        """Raise the appropriate exception for magic channel trigger values.

        Args:
            channel_name: The channel name to inspect.

        Raises:
            discord.Forbidden: When ``channel_name == "chan-forbidden"``.
            discord.HTTPException: For ``"chan-http4xx"`` or
                ``"chan-http5xx"``.
            asyncio.TimeoutError: For ``"chan-timeout"``.
        """
        if channel_name == "chan-forbidden":
            response = MagicMock()
            response.status = 403
            response.reason = "Forbidden"
            raise discord.Forbidden(response, "Missing Permissions")
        if channel_name == "chan-http4xx":
            raise _make_discord_http_exc(429, "You are being rate limited")
        if channel_name == "chan-http5xx":
            raise _make_discord_http_exc(500, "Internal Server Error")
        if channel_name == "chan-timeout":
            raise TimeoutError()
