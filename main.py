import os
import json
import logging
import asyncio
import re
from copy import deepcopy
from typing import Optional, Dict, Any

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

# ============================================================
# OPTIC SUPPLY TICKET BOT
# Python 3.10+ | discord.py 2.3+
#
# Install:
#   pip install -U discord.py python-dotenv
#
# .env:
#   DISCORD_TOKEN=YOUR_BOT_TOKEN
#
# Run:
#   python main.py
# ============================================================

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("OpticTicketBot")

CONFIG_FILE = os.getenv("CONFIG_PATH", "ticket_configs.json")
os.makedirs(os.path.dirname(CONFIG_FILE) or ".", exist_ok=True)
BOT_VERSION = "3.0.0"

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True


DEFAULT_CONFIG: Dict[str, Any] = {
    "ticket_category_id": None,
    "default_staff_role_id": None,
    "panel_channel_id": None,
    "log_channel_id": None,
    "panel_message_id": None,

    "ticket_counter": 0,
    "access_roles": {},

    # Panel appearance
    "panel_title": "OPTIC SUPPLY | Support Center",
    "panel_description": (
        "Welcome to our support center.\n\n"
        "Need help or want to report something? Select an option below to continue."
    ),
    "panel_color": 0x5865F2,
    "logo_url": None,
    "banner_url": None,
    "panel_author_name": "OPTIC SUPPLY",
    "panel_author_icon": None,
    "panel_footer": "OPTIC SUPPLY • Official Support Center",
    "panel_footer_icon": None,
    "dropdown_placeholder": "Select an option...",

    # Dropdown choices
    "support_label": "Support",
    "support_description": "Open a ticket for help or inquiries.",
    "support_emoji": "🎫",
    "report_label": "Report",
    "report_description": "Report a user or an issue.",
    "report_emoji": "🚨",

    # Ticket messages
    "ticket_welcome_title": "{ticket_type} Ticket #{ticket_number}",
    "ticket_welcome_message": (
        "Welcome {user}!\n\n"
        "Please describe your {ticket_type} request. A staff member will assist you shortly."
    ),
    "claim_message": "This ticket has been claimed by {staff}.",
    "unclaim_message": "This ticket is no longer claimed.",
    "close_message": "This ticket will be closed in 5 seconds.",
    "ticket_prefix_support": "support",
    "ticket_prefix_report": "report",
}


def load_all_configs() -> Dict[str, Dict[str, Any]]:
    if not os.path.exists(CONFIG_FILE):
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as exc:
        logger.exception("Could not load config file: %s", exc)
        return {}


class TicketBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="$",
            intents=intents,
            help_command=None,
        )
        self.configs: Dict[str, Dict[str, Any]] = load_all_configs()

    async def setup_hook(self):
        # Persistent views allow buttons/select menus to work after restarts.
        self.add_view(PersistentTicketPanelView(self))
        self.add_view(PersistentTicketActionsView(self))
        try:
            synced = await self.tree.sync()
            logger.info("Synced %s slash commands.", len(synced))
        except Exception:
            logger.exception("Slash-command sync failed.")


bot = TicketBot()


def get_guild_config(guild_id: int) -> Dict[str, Any]:
    key = str(guild_id)
    if key not in bot.configs:
        bot.configs[key] = deepcopy(DEFAULT_CONFIG)

    # Add defaults introduced by newer bot versions without overwriting settings.
    for name, value in DEFAULT_CONFIG.items():
        if name not in bot.configs[key]:
            bot.configs[key][name] = deepcopy(value)

    if not isinstance(bot.configs[key].get("access_roles"), dict):
        bot.configs[key]["access_roles"] = {}

    return bot.configs[key]


def save_all_configs() -> None:
    temp_file = CONFIG_FILE + ".tmp"
    with open(temp_file, "w", encoding="utf-8") as file:
        json.dump(bot.configs, file, indent=2, ensure_ascii=False)
    os.replace(temp_file, CONFIG_FILE)


def valid_http_url(value: Optional[str]) -> bool:
    if not value:
        return True
    return value.startswith("https://") or value.startswith("http://")


def safe_channel_name(value: str) -> str:
    value = value.lower().strip().replace(" ", "-")
    value = re.sub(r"[^a-z0-9_-]", "", value)
    return value[:80] or "ticket"


def build_panel_embed(config: Dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=config.get("panel_title") or "Support Center",
        description=config.get("panel_description") or "",
        color=int(config.get("panel_color", 0x5865F2)),
    )

    if config.get("logo_url"):
        embed.set_thumbnail(url=config["logo_url"])

    if config.get("banner_url"):
        embed.set_image(url=config["banner_url"])

    author_name = config.get("panel_author_name")
    if author_name:
        icon = config.get("panel_author_icon")
        if icon:
            embed.set_author(name=author_name, icon_url=icon)
        else:
            embed.set_author(name=author_name)

    footer = config.get("panel_footer")
    if footer:
        footer_icon = config.get("panel_footer_icon")
        if footer_icon:
            embed.set_footer(text=footer, icon_url=footer_icon)
        else:
            embed.set_footer(text=footer)

    return embed


def build_panel_view(config: Dict[str, Any]) -> discord.ui.View:
    return PersistentTicketPanelView(bot, config)


async def refresh_panel(guild: discord.Guild) -> bool:
    config = get_guild_config(guild.id)
    channel_id = config.get("panel_channel_id")
    message_id = config.get("panel_message_id")

    if not channel_id or not message_id:
        return False

    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        message = await channel.fetch_message(int(message_id))
        await message.edit(
            embed=build_panel_embed(config),
            view=build_panel_view(config),
        )
        return True
    except discord.NotFound:
        return False
    except discord.Forbidden:
        logger.warning("Missing permission to edit panel in %s.", guild.name)
        return False
    except discord.HTTPException:
        logger.exception("Could not refresh panel.")
        return False


def get_member_access(member: discord.Member, guild: discord.Guild) -> Optional[str]:
    if member.guild_permissions.administrator:
        return "both"

    config = get_guild_config(guild.id)
    staff_role_id = config.get("default_staff_role_id")
    if staff_role_id:
        role = guild.get_role(int(staff_role_id))
        if role and role in member.roles:
            return "both"

    result = None
    for role_id, level in config.get("access_roles", {}).items():
        role = guild.get_role(int(role_id))
        if role and role in member.roles:
            if level == "both":
                return "both"
            if level == "claim":
                result = "claim"
            elif level == "view" and result is None:
                result = "view"
    return result


def is_ticket_channel(channel: discord.abc.GuildChannel) -> bool:
    return isinstance(channel, discord.TextChannel) and bool(
        channel.topic and "Owner ID:" in channel.topic
    )


def get_ticket_owner_id(channel: discord.TextChannel) -> Optional[int]:
    if not channel.topic:
        return None
    match = re.search(r"Owner ID:\s*(\d+)", channel.topic)
    return int(match.group(1)) if match else None


def get_claimed_id(channel: discord.TextChannel) -> Optional[int]:
    if not channel.topic:
        return None
    match = re.search(r"Claimed by:\s*(\d+)", channel.topic)
    return int(match.group(1)) if match else None


async def send_log(
    guild: discord.Guild,
    title: str,
    description: str,
    color: int = 0x5865F2,
) -> None:
    config = get_guild_config(guild.id)
    channel_id = config.get("log_channel_id")
    channel = guild.get_channel(int(channel_id)) if channel_id else None

    if not isinstance(channel, discord.TextChannel):
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if config.get("logo_url"):
        embed.set_thumbnail(url=config["logo_url"])
    if config.get("banner_url"):
        embed.set_image(url=config["banner_url"])

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.exception("Could not send ticket log.")


class PersistentTicketPanelView(discord.ui.View):
    def __init__(
        self,
        bot_instance: commands.Bot,
        config: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance
        config = config or DEFAULT_CONFIG
        self.add_item(TicketTypeSelect(config))


class TicketTypeSelect(discord.ui.Select):
    def __init__(self, config: Dict[str, Any]):
        options = [
            discord.SelectOption(
                label=str(config.get("support_label", "Support"))[:100],
                description=str(config.get("support_description", "Open a support ticket."))[:100],
                emoji=config.get("support_emoji") or None,
                value="support",
            ),
            discord.SelectOption(
                label=str(config.get("report_label", "Report"))[:100],
                description=str(config.get("report_description", "Report an issue."))[:100],
                emoji=config.get("report_emoji") or None,
                value="report",
            ),
        ]

        super().__init__(
            placeholder=str(config.get("dropdown_placeholder", "Select an option..."))[:150],
            min_values=1,
            max_values=1,
            options=options,
            custom_id="optic_ticket_type_select",
        )

    async def callback(self, interaction: discord.Interaction):
        await create_ticket(interaction, self.values[0])


class PersistentTicketActionsView(discord.ui.View):
    def __init__(self, bot_instance: commands.Bot):
        super().__init__(timeout=None)
        self.bot_instance = bot_instance

    @discord.ui.button(
        label="Claim",
        style=discord.ButtonStyle.success,
        custom_id="optic_ticket_claim",
        emoji="🔒",
    )
    async def claim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await handle_claim(interaction)

    @discord.ui.button(
        label="Unclaim",
        style=discord.ButtonStyle.secondary,
        custom_id="optic_ticket_unclaim",
        emoji="🔓",
    )
    async def unclaim_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await handle_unclaim(interaction)

    @discord.ui.button(
        label="Close",
        style=discord.ButtonStyle.danger,
        custom_id="optic_ticket_close",
        emoji="✖️",
    )
    async def close_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ):
        await handle_close(interaction)


async def create_ticket(interaction: discord.Interaction, ticket_type: str):
    guild = interaction.guild
    if guild is None or not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Tickets can only be opened inside a server.",
            ephemeral=True,
        )
        return

    config = get_guild_config(guild.id)
    category_id = config.get("ticket_category_id")
    category = guild.get_channel(int(category_id)) if category_id else None

    if not isinstance(category, discord.CategoryChannel):
        await interaction.response.send_message(
            "The ticket system is not configured. An administrator must run /setup.",
            ephemeral=True,
        )
        return

    # One open ticket per member across the configured ticket category.
    for channel in category.text_channels:
        if get_ticket_owner_id(channel) == interaction.user.id:
            await interaction.response.send_message(
                f"You already have an open ticket: {channel.mention}",
                ephemeral=True,
            )
            return

    await interaction.response.defer(ephemeral=True, thinking=True)

    config["ticket_counter"] = int(config.get("ticket_counter", 0)) + 1
    ticket_number = config["ticket_counter"]
    save_all_configs()

    prefix_key = "ticket_prefix_report" if ticket_type == "report" else "ticket_prefix_support"
    prefix = safe_channel_name(config.get(prefix_key, ticket_type))
    channel_name = f"{prefix}-{ticket_number:04d}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        ),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            attach_files=True,
            embed_links=True,
        ),
    }

    staff_role_id = config.get("default_staff_role_id")
    if staff_role_id:
        staff_role = guild.get_role(int(staff_role_id))
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    # Roles with view/claim/both access can see ticket channels.
    for role_id in config.get("access_roles", {}):
        role = guild.get_role(int(role_id))
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    try:
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=(
                f"Type: {ticket_type} | Ticket: {ticket_number:04d} | "
                f"Owner ID: {interaction.user.id}"
            ),
            reason=f"{ticket_type.title()} ticket opened by {interaction.user}",
        )

        welcome_title = str(config.get("ticket_welcome_title", "Ticket #{ticket_number}"))
        welcome_description = str(config.get("ticket_welcome_message", "Welcome {user}!"))
        replacements = {
            "user": interaction.user.mention,
            "ticket_type": ticket_type.capitalize(),
            "ticket_number": f"{ticket_number:04d}",
        }

        try:
            welcome_title = welcome_title.format(**replacements)
            welcome_description = welcome_description.format(**replacements)
        except (KeyError, ValueError):
            pass

        embed = discord.Embed(
            title=welcome_title[:256],
            description=welcome_description[:4000],
            color=int(config.get("panel_color", 0x5865F2)),
        )
        if config.get("logo_url"):
            embed.set_thumbnail(url=config["logo_url"])
        if config.get("banner_url"):
            embed.set_image(url=config["banner_url"])

        await ticket_channel.send(
            content=interaction.user.mention,
            embed=embed,
            view=PersistentTicketActionsView(bot),
        )

        await interaction.followup.send(
            f"Your {ticket_type} ticket has been created: {ticket_channel.mention}",
            ephemeral=True,
        )
        await send_log(
            guild,
            "Ticket opened",
            f"**Type:** {ticket_type.title()}\n"
            f"**User:** {interaction.user.mention}\n"
            f"**Channel:** {ticket_channel.mention}",
            0x2ECC71,
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "I cannot create the ticket channel. Check my Manage Channels permission and role position.",
            ephemeral=True,
        )
    except discord.HTTPException:
        logger.exception("Ticket creation failed.")
        await interaction.followup.send(
            "Something went wrong while creating your ticket.",
            ephemeral=True,
        )


async def handle_claim(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
        await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True)
        return

    access = get_member_access(interaction.user, guild)
    if access not in ("claim", "both"):
        await interaction.response.send_message("You do not have permission to claim tickets.", ephemeral=True)
        return

    if get_claimed_id(channel):
        await interaction.response.send_message("This ticket has already been claimed.", ephemeral=True)
        return

    new_topic = (channel.topic or "") + f" | Claimed by: {interaction.user.id}"
    await channel.edit(topic=new_topic, reason=f"Ticket claimed by {interaction.user}")
    config = get_guild_config(guild.id)
    message = str(config.get("claim_message", "Ticket claimed by {staff}."))
    message = message.replace("{staff}", interaction.user.mention)

    await interaction.response.send_message(message)
    await send_log(guild, "Ticket claimed", f"{channel.mention} claimed by {interaction.user.mention}")


async def handle_unclaim(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
        await interaction.response.send_message("Use this inside a ticket channel.", ephemeral=True)
        return

    access = get_member_access(interaction.user, guild)
    if access not in ("claim", "both"):
        await interaction.response.send_message("You do not have permission to unclaim tickets.", ephemeral=True)
        return

    claimed_id = get_claimed_id(channel)
    if not claimed_id:
        await interaction.response.send_message("This ticket is not claimed.", ephemeral=True)
        return

    # Only the claimer or an administrator can unclaim it.
    if claimed_id != interaction.user.id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only the person who claimed this ticket or an administrator can unclaim it.", ephemeral=True)
        return

    new_topic = re.sub(r"\s*\|\s*Claimed by:\s*\d+", "", channel.topic or "")
    await channel.edit(topic=new_topic, reason=f"Ticket unclaimed by {interaction.user}")

    config = get_guild_config(guild.id)
    await interaction.response.send_message(config.get("unclaim_message", "Ticket unclaimed."))
    await send_log(guild, "Ticket unclaimed", f"{channel.mention} unclaimed by {interaction.user.mention}")


async def handle_close(interaction: discord.Interaction):
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel) or not is_ticket_channel(channel):
        await interaction.response.send_message("This is not a 
