import os
import json
import logging
import asyncio
from typing import Optional, Dict, Any, List

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    print("CRITICAL ERROR: DISCORD_TOKEN environment variable is missing!")
    exit(1)

# Configure Logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("TicketBot")

intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True
intents.guild_messages = True

BOT_VERSION = "2.1.0"

class TicketBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="$", intents=intents, help_command=None)
        # guild_id -> config dict
        self.configs: Dict[int, Dict[str, Any]] = {}

    async def setup_hook(self):
        # Register persistent views
        self.add_view(PersistentTicketPanelView(self))
        self.add_view(PersistentTicketActionsView(self))
        try:
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands globally.")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

bot = TicketBot()

# --- CONFIGURATION & BACKUP SYSTEM ---

DEFAULT_CONFIG = {
    "ticket_category_id": None,
    "default_staff_role_id": None,
    "panel_channel_id": None,
    "log_channel_id": None,
    "config_channel_id": None,
    "access_roles": {},      # role_id_str -> "view" | "claim" | "both"
    "ticket_counter": 0,
    "logo_url": None,
    "banner_url": None,
    "panel_title": "Support Ticket Center",
    "panel_description": "Click the button below to open a private support ticket with our staff team.",
    "panel_button_label": "Create Ticket",
    "panel_color": 0x5865F2,
    "panel_message_id": None
}

def get_guild_config(guild_id: int) -> Dict[str, Any]:
    if guild_id not in bot.configs:
        bot.configs[guild_id] = DEFAULT_CONFIG.copy()
        bot.configs[guild_id]["access_roles"] = {}
    return bot.configs[guild_id]

async def save_guild_config(guild: discord.guild.Guild):
    config = get_guild_config(guild.id)
    config_channel_id = config.get("config_channel_id")
    channel = None

    if config_channel_id:
        channel = guild.get_channel(config_channel_id)

    if not channel:
        # Search for existing config channel
        for ch in guild.text_channels:
            if ch.name == "ticket-bot-config":
                channel = ch
                config["config_channel_id"] = ch.id
                break

    if not channel:
        # Create private config channel
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        # Add staff role if exists
        staff_role_id = config.get("default_staff_role_id")
        if staff_role_id:
            staff_role = guild.get_role(staff_role_id)
            if staff_role:
                overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            channel = await guild.create_text_channel(
                name="ticket-bot-config",
                overwrites=overwrites,
                topic="[SYSTEM] Bot configuration backup channel. Do not delete or modify messages!"
            )
            config["config_channel_id"] = channel.id
            logger.info(f"Created config backup channel in guild: {guild.name} ({guild.id})")
        except Exception as e:
            logger.error(f"Failed to create config channel in {guild.name}: {e}")
            return

    # Prepare JSON payload (exclude secrets/tokens)
    export_data = config.copy()
    payload = json.dumps(export_data, indent=2)

    try:
        messages = [m async for m in channel.history(limit=10)]
        bot_messages = [m for m in messages if m.author == guild.me]

        content_msg = f"```json\n{payload}\n```"
        if bot_messages:
            await bot_messages[0].edit(content=content_msg)
        else:
            await channel.send(content=content_msg)
        logger.info(f"Configuration successfully backed up to Discord for guild: {guild.name}")
    except Exception as e:
        logger.error(f"Failed to save configuration backup for {guild.name}: {e}")

async def restore_guild_config(guild: discord.guild.Guild):
    config = get_guild_config(guild.id)
    target_channel = None

    for ch in guild.text_channels:
        if ch.name == "ticket-bot-config":
            target_channel = ch
            config["config_channel_id"] = ch.id
            break

    if not target_channel:
        logger.info(f"No config backup channel found for {guild.name}.")
        return

    try:
        messages = [m async for m in target_channel.history(limit=10) if m.author == guild.me]
        if not messages:
            return

        msg = messages[0]
        content = msg.content
        if "```json" in content:
            json_str = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_str = content.split("```")[1].split("```")[0].strip()
        else:
            json_str = content.strip()

        data = json.loads(json_str)
        if isinstance(data, dict):
            for k, v in data.items():
                if k in config:
                    config[k] = v
            logger.info(f"Successfully restored configuration for guild: {guild.name}")
    except Exception as e:
        logger.error(f"Failed to restore configuration for {guild.name}: {e}")

@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user.name} (ID: {bot.user.id})")
    logger.info(f"discord.py version: {discord.__version__}")
    logger.info("Restoring configurations across connected guilds...")
    for guild in bot.guilds:
        await restore_guild_config(guild)
    logger.info("Bot is fully online and operational.")

@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info(f"Joined new guild: {guild.name} ({guild.id})")
    await save_guild_config(guild)

# --- LOGGING HELPER ---

async def send_log_embed(guild: discord.Guild, title: str, description: str, color: int = 0x5865F2, fields: List[Dict[str, Any]] = None):
    config = get_guild_config(guild.id)
    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return
    channel = guild.get_channel(log_channel_id)
    if not channel:
        return

    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    logo_url = config.get("logo_url")
    if logo_url:
        embed.set_thumbnail(url=logo_url)
    
    if fields:
        for f in fields:
            embed.add_field(name=f.get("name"), value=f.get("value"), inline=f.get("inline", False))

    banner_url = config.get("banner_url")
    if banner_url:
        embed.set_image(url=banner_url)

    try:
        await channel.send(embed=embed)
    except Exception as e:
        logger.error(f"Failed to send log embed in {guild.name}: {e}")

# --- PERMISSION CHECKS ---

def is_staff_or_admin():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if interaction.user.guild_permissions.administrator:
            return True
        config = get_guild_config(interaction.guild.id)
        staff_role_id = config.get("default_staff_role_id")
        if staff_role_id and interaction.guild.get_role(staff_role_id) in interaction.user.roles:
            return True
        
        access_roles = config.get("access_roles", {})
        for r_id_str, level in access_roles.items():
            if level in ["claim", "both"]:
                role = interaction.guild.get_role(int(r_id_str))
                if role and role in interaction.user.roles:
                    return True
        return False
    return app_commands.check(predicate)

def has_ticket_access(member: discord.Member, guild: discord.Guild) -> str:
    if member.guild_permissions.administrator:
        return "admin"
    config = get_guild_config(guild.id)
    staff_role_id = config.get("default_staff_role_id")
    if staff_role_id and guild.get_role(staff_role_id) in member.roles:
        return "claim"

    access_roles = config.get("access_roles", {})
    level = None
    for r_id_str, lvl in access_roles.items():
        role = guild.get_role(int(r_id_str))
        if role and role in member.roles:
            if lvl == "both":
                return "claim"
            elif lvl == "claim":
                level = "claim"
            elif lvl == "view" and not level:
                level = "view"
    return level

# --- TICKET UI VIEWS ---

class PersistentTicketPanelView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Ticket", style=discord.ButtonStyle.primary, custom_id="ticket_panel_create_btn", emoji="🎫")
    async def create_ticket_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_ticket_creation(interaction)

class PersistentTicketActionsView(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Claim", style=discord.ButtonStyle.success, custom_id="ticket_action_claim", emoji="🔒")
    async def claim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_claim(interaction)

    @discord.ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, custom_id="ticket_action_unclaim", emoji="🔓")
    async def unclaim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_unclaim(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.danger, custom_id="ticket_action_close", emoji="✖️")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await handle_close(interaction)

# --- CORE TICKET LOGIC ---

async def handle_ticket_creation(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    config = get_guild_config(guild.id)
    category_id = config.get("ticket_category_id")
    category = guild.get_channel(category_id) if category_id else None

    if not category:
        await interaction.response.send_message("Ticket system is not fully configured. Please ask an administrator to run `/setup`.", ephemeral=True)
        return

    for ch in category.text_channels:
        if ch.topic and f"Owner ID: {interaction.user.id}" in ch.topic:
            await interaction.response.send_message(f"You already have an open ticket: {ch.mention}", ephemeral=True)
            return

    await interaction.response.defer(ephemeral=True)

    config["ticket_counter"] = config.get("ticket_counter", 0) + 1
    ticket_num = config["ticket_counter"]
    await save_guild_config(guild)

    channel_name = f"ticket-{ticket_num:04d}"

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_permissions=True),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True, embed_links=True)
    }

    staff_role_id = config.get("default_staff_role_id")
    if staff_role_id:
        staff_role = guild.get_role(staff_role_id)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)

    access_roles = config.get("access_roles", {})
    for r_id_str, level in access_roles.items():
        role = guild.get_role(int(r_id_str))
        if role:
            if level in ["claim", "both"]:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
            elif level == "view":
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=False)

    try:
        ticket_channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Support Ticket #{ticket_num:04d} | Owner ID: {interaction.user.id} | Created by {interaction.user}"
        )
    except Exception as e:
        logger.error(f"Failed to create ticket channel in {guild.name}: {e}")
        await interaction.followup.send("Failed to create ticket channel due to missing permissions or category limits.", ephemeral=True)
        return

    embed = discord.Embed(
        title=f"Ticket #{ticket_num:04d}",
        description=f"Welcome {interaction.user.mention}!\n\nPlease describe your issue or question in detail. Our support team will be with you shortly.\n\nUse the buttons below to manage this ticket.",
        color=config.get("panel_color", 0x5865F2),
        timestamp=discord.utils.utcnow()
    )
    logo_url = config.get("logo_url")
    if logo_url:
        embed.set_thumbnail(url=logo_url)
    
    embed.add_field(name="Ticket Owner", value=interaction.user.mention, inline=True)
    embed.add_field(name="Status", value="Open (Unclaimed)", inline=True)

    banner_url = config.get("banner_url")
    if banner_url:
        embed.set_image(url=banner_url)

    view = PersistentTicketActionsView(bot)
    await ticket_channel.send(content=f"{interaction.user.mention}", embed=embed, view=view)
    await interaction.followup.send(f"Your ticket has been created: {ticket_channel.mention}", ephemeral=True)

    await send_log_embed(
        guild=guild,
        title="Ticket Created",
        description=f"Ticket {ticket_channel.mention} was created by {interaction.user.mention}.",
        color=0x2ECC71,
        fields=[
            {"name": "Ticket", "value": f"#{ticket_num:04d}", "inline": True},
            {"name": "Owner", "value": f"{interaction.user} (`{interaction.user.id}`)", "inline": True}
        ]
    )

async def handle_claim(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or not interaction.channel:
        return

    access = has_ticket_access(interaction.user, guild)
    if not access or access == "view":
        await interaction.response.send_message("You do not have permission to claim tickets.", ephemeral=True)
        return

    await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.", ephemeral=False)
    await send_log_embed(
        guild=guild,
        title="Ticket Claimed",
        description=f"Ticket {interaction.channel.mention} was claimed by {interaction.user.mention}.",
        color=0xF1C40F,
        fields=[{"name": "Claimed By", "value": f"{interaction.user} (`{interaction.user.id}`)", "inline": True}]
    )

async def handle_unclaim(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or not interaction.channel:
        return

    access = has_ticket_access(interaction.user, guild)
    if not access or access == "view":
        await interaction.response.send_message("You do not have permission to unclaim tickets.", ephemeral=True)
        return

    await interaction.response.send_message(f"Ticket unclaimed by {interaction.user.mention}.", ephemeral=False)
    await send_log_embed(
        guild=guild,
        title="Ticket Unclaimed",
        description=f"Ticket {interaction.channel.mention} was unclaimed by {interaction.user.mention}.",
        color=0xE67E22,
        fields=[{"name": "Unclaimed By", "value": f"{interaction.user} (`{interaction.user.id}`)", "inline": True}]
    )

async def handle_close(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or not interaction.channel:
        return

    is_owner = False
    if interaction.channel.topic and f"Owner ID: {interaction.user.id}" in interaction.channel.topic:
        is_owner = True

    access = has_ticket_access(interaction.user, guild)
    if not is_owner and not access:
        await interaction.response.send_message("You do not have permission to close this ticket.", ephemeral=True)
        return

    await interaction.response.send_message("Closing ticket in 3 seconds...", ephemeral=False)
    await send_log_embed(
        guild=guild,
        title="Ticket Closed",
        description=f"Ticket `{interaction.channel.name}` was closed by {interaction.user.mention}.",
        color=0xE74C3C,
        fields=[{"name": "Closed By", "value": f"{interaction.user} (`{interaction.user.id}`)", "inline": True}]
    )

    await asyncio.sleep(3)
    try:
        await interaction.channel.delete(reason=f"Ticket closed by {interaction.user}")
    except Exception as e:
        logger.error(f"Failed to delete ticket channel: {e}")

# --- SLASH COMMANDS: SETUP & PANEL ---

@bot.tree.command(name="setup", description="Configure the ticket system for this server.")
@app_commands.describe(
    category="The category where tickets will be created.",
    staff_role="The default staff role with full ticket management access.",
    panel_channel="Optional channel where the ticket panel will be posted.",
    log_channel="Optional channel where ticket audit logs will be sent."
)
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(
    interaction: discord.Interaction,
    category: discord.CategoryChannel,
    staff_role: discord.Role,
    panel_channel: Optional[discord.TextChannel] = None,
    log_channel: Optional[discord.TextChannel] = None
):
    guild = interaction.guild
    if not guild:
        return

    config = get_guild_config(guild.id)
    config["ticket_category_id"] = category.id
    config["default_staff_role_id"] = staff_role.id
    if panel_channel:
        config["panel_channel_id"] = panel_channel.id
    if log_channel:
        config["log_channel_id"] = log_channel.id

    await save_guild_config(guild)

    embed = discord.Embed(
        title="Ticket System Setup Complete",
        description="Your server has been successfully configured for the ticket system.",
        color=0x2ECC71,
        timestamp=discord.utils.utcnow()
    )
    embed.add_field(name="Ticket Category", value=category.mention, inline=False)
    embed.add_field(name="Default Staff Role", value=staff_role.mention, inline=False)
    if panel_channel:
        embed.add_field(name="Panel Channel", value=panel_channel.mention, inline=False)
    if log_channel:
        embed.add_field(name="Log Channel", value=log_channel.mention, inline=False)

    embed.set_footer(text="Settings are automatically backed up and will persist across bot restarts.")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@setup_command.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("You must be a server administrator to run `/setup`.", ephemeral=True)
    else:
        await interaction.response.send_message(f"An error occurred: {error}", ephemeral=True)

@bot.tree.command(name="panel", description="Create or manage the ticket creation panel.")
@app_commands.describe(action="Choose whether to create or edit the ticket panel.")
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="edit"
