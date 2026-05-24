"""Discord client for the Siege Assignment System."""

import io
from typing import Literal

import discord


class SiegeBot(discord.Client):
    """Discord client for the Siege Assignment System."""

    def __init__(self, guild_id: int, **kwargs):
        super().__init__(**kwargs)
        self.guild_id = guild_id
        self._guild: discord.Guild | None = None

    async def on_ready(self):
        self._guild = self.get_guild(self.guild_id)

    def _require_guild(self) -> discord.Guild:
        if self._guild is None:
            raise RuntimeError("Bot not ready — guild not loaded yet")
        return self._guild

    async def send_dm(self, username: str, message: str) -> None:
        """Find member by username in the guild, open DM, send message."""
        guild = self._require_guild()
        member = discord.utils.find(lambda m: m.name.lower() == username.lower(), guild.members)
        if member is None:
            raise ValueError(f"Member '{username}' not found in guild")
        dm = await member.create_dm()
        await dm.send(message)

    async def post_message(self, channel_name: str, message: str) -> None:
        """Find text channel by name, post message."""
        guild = self._require_guild()
        channel = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name == channel_name,
            guild.channels,
        )
        if channel is None:
            raise ValueError(f"Channel '{channel_name}' not found in guild")
        await channel.send(message)

    async def post_image(
        self, channel_name: str, image_bytes: bytes, filename: str = "image.png"
    ) -> str:
        """Find text channel by name, post image as Discord file attachment.

        Returns the Discord CDN URL of the posted attachment.
        """
        guild = self._require_guild()
        channel = discord.utils.find(
            lambda c: isinstance(c, discord.TextChannel) and c.name == channel_name,
            guild.channels,
        )
        if channel is None:
            raise ValueError(f"Channel '{channel_name}' not found in guild")
        msg = await channel.send(file=discord.File(io.BytesIO(image_bytes), filename=filename))
        return msg.attachments[0].url

    async def get_members(self) -> list[dict]:
        """Return list of guild members as dicts with id, username, and display_name."""
        guild = self._require_guild()
        return [
            {
                "id": str(m.id),
                "username": m.name,
                "display_name": m.display_name,
            }
            for m in guild.members
        ]

    async def apply_day_role(
        self,
        discord_id: str,
        role_id: str,
        action: Literal["assign", "unassign"],
    ) -> tuple[str, str]:
        """Toggle a day role for a guild member.

        Fetches the member by snowflake ID and applies or removes the
        specified role.  Both ``add_roles`` and ``remove_roles`` are
        idempotent at the Discord API level, so no prior-state check is
        needed.

        Args:
            discord_id: Discord snowflake string of the target member.
            role_id: Discord snowflake string of the role to toggle.
            action: ``"assign"`` adds the role; ``"unassign"`` removes it.

        Returns:
            A 2-tuple ``(status, role_name)`` where ``status`` is always
            ``"applied"`` and ``role_name`` is the Discord role's display
            name (used by the HTTP route to populate the ``added`` /
            ``removed`` fields in the response body).

        Raises:
            discord.NotFound: When the member is not in the guild.
            discord.Forbidden: When the bot lacks permission to manage
                the role (e.g. role is above the bot in the hierarchy).
            discord.HTTPException: On other Discord API errors.
            ValueError: When ``role_id`` resolves to ``None`` in the
                guild (role does not exist or was deleted).
        """
        guild = self._require_guild()
        member = await guild.fetch_member(int(discord_id))
        role = guild.get_role(int(role_id))
        if role is None:
            raise ValueError(f"Role '{role_id}' not found in guild")
        if action == "assign":
            await member.add_roles(role)
        else:
            await member.remove_roles(role)
        return "applied", role.name
