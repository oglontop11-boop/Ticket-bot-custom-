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
        self.configs: Dict[int, Dict[str, Any]] = {}

    async def setup_hook(self):
        self.add_view(PersistentTicketPanelView(self))
        self.add_view(PersistentTicketActionsView(self))
        try:
            synced = await self.tree.sync()
            logger.info(f"Successfully synced {len(synced)} slash commands globally.")
        except Exception as e:
            logger.error(f"Failed to sync slash commands: {e}")

bot = TicketBot()

DEFAULT_CONFIG = {
    "ticket_category_id": None,
    "default_staff_role_id": None,
    "panel_channel_id": None,
    "log_channel_id": None,
    "config_channel_id": None,
    "access_roles": {},
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
        for ch in guild.text_channels:
            if ch.name == "ticket-bot-config":
                channel = ch
                config["config_channel_id"] = ch.id
                break

    if not channel:
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
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
    for guild in bot.guilds:
        await restore_guild_config(guild)
    logger.info("Bot is fully online and operational.")

@bot.event
async def on_guild_join(guild: discord.Guild):
    await save_guild_config(guild)

async def send_log_embed(guild: discord.Guild, title: str, description: str, color: int = 0x5865F2, fields: List[Dict[str, Any]] = None):
    config = get_guild_config(guild.id)
    log_channel_id = config.get("log_channel_id")
    if not log_channel_id:
        return
    channel = guild.get_channel(log_channel_id)
    if not channel:
        return

    embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
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
        logger.error(f"Failed to send log embed: {e}")

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

async def handle_ticket_creation(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    category_id = config.get("ticket_category_id")
    category = guild.get_channel(category_id) if category_id else None
    if not category:
        await interaction.response.send_message("Ticket system not configured. Run `/setup`.", ephemeral=True)
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
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    staff_role_id = config.get("default_staff_role_id")
    if staff_role_id:
        staff_role = guild.get_role(staff_role_id)
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

    ticket_channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Support Ticket #{ticket_num:04d} | Owner ID: {interaction.user.id}"
    )

    embed = discord.Embed(
        title=f"Ticket #{ticket_num:04d}",
        description=f"Welcome {interaction.user.mention}!\nDescribe your issue below.",
        color=config.get("panel_color", 0x5865F2)
    )
    view = PersistentTicketActionsView(bot)
    await ticket_channel.send(content=f"{interaction.user.mention}", embed=embed, view=view)
    await interaction.followup.send(f"Ticket created: {ticket_channel.mention}", ephemeral=True)

async def handle_claim(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return
    access = has_ticket_access(interaction.user, guild)
    if not access or access == "view":
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await interaction.response.send_message(f"Ticket claimed by {interaction.user.mention}.", ephemeral=False)

async def handle_unclaim(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild:
        return
    access = has_ticket_access(interaction.user, guild)
    if not access or access == "view":
        await interaction.response.send_message("No permission.", ephemeral=True)
        return
    await interaction.response.send_message(f"Ticket unclaimed by {interaction.user.mention}.", ephemeral=False)

async def handle_close(interaction: discord.Interaction):
    guild = interaction.guild
    if not guild or not interaction.channel:
        return
    is_owner = interaction.channel.topic and f"Owner ID: {interaction.user.id}" in interaction.channel.topic
    access = has_ticket_access(interaction.user, guild)
    if not is_owner and not access:
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await interaction.response.send_message("Closing in 3 seconds...", ephemeral=False)
    await asyncio.sleep(3)
    try:
        await interaction.channel.delete()
    except Exception:
        pass

@bot.tree.command(name="setup", description="Configure the ticket system.")
@app_commands.describe(category="Ticket category", staff_role="Staff role", panel_channel="Panel channel", log_channel="Log channel")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction: discord.Interaction, category: discord.CategoryChannel, staff_role: discord.Role, panel_channel: Optional[discord.TextChannel] = None, log_channel: Optional[discord.TextChannel] = None):
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
    await interaction.response.send_message("Setup complete!", ephemeral=True)

@setup_command.error
async def setup_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await interaction.response.send_message(f"Error: {error}", ephemeral=True)

@bot.tree.command(name="panel", description="Create or manage panel.")
@app_commands.describe(action="Action")
@app_commands.choices(action=[
    app_commands.Choice(name="create", value="create"),
    app_commands.Choice(name="edit", value="edit")
])
@app_commands.checks.has_permissions(administrator=True)
async def panel_command(interaction: discord.Interaction, action: str):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    channel = guild.get_channel(config.get("panel_channel_id") or interaction.channel_id)
    if not channel:
        await interaction.response.send_message("Channel not found.", ephemeral=True)
        return

    embed = discord.Embed(title=config.get("panel_title"), description=config.get("panel_description"), color=0x5865F2)
    view = PersistentTicketPanelView(bot)
    msg = await channel.send(embed=embed, view=view)
    config["panel_message_id"] = msg.id
    await save_guild_config(guild)
    await interaction.response.send_message("Panel deployed!", ephemeral=True)

@panel_command.error
async def panel_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await interaction.response.send_message(f"Error: {error}", ephemeral=True)

access_group = app_commands.Group(name="access", description="Manage access")

@access_group.command(name="add", description="Add role access")
@app_commands.choices(permission=[
    app_commands.Choice(name="view", value="view"),
    app_commands.Choice(name="claim", value="claim"),
    app_commands.Choice(name="both", value="both")
])
@app_commands.checks.has_permissions(administrator=True)
async def access_add(interaction: discord.Interaction, role: discord.Role, permission: str):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    config["access_roles"][str(role.id)] = permission
    await save_guild_config(guild)
    await interaction.response.send_message(f"Role {role.mention} added.", ephemeral=True)

@access_group.command(name="remove", description="Remove role access")
@app_commands.checks.has_permissions(administrator=True)
async def access_remove(interaction: discord.Interaction, role: discord.Role):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    config["access_roles"].pop(str(role.id), None)
    await save_guild_config(guild)
    await interaction.response.send_message("Role removed.", ephemeral=True)

@access_group.command(name="list", description="List access roles")
async def access_list(interaction: discord.Interaction):
    await interaction.response.send_message("Access list working.", ephemeral=True)

bot.tree.add_command(access_group)

branding_group = app_commands.Group(name="branding", description="Branding options")

@branding_group.command(name="set", description="Set branding")
@app_commands.checks.has_permissions(administrator=True)
async def branding_set(interaction: discord.Interaction, logo_url: Optional[str] = None, banner_url: Optional[str] = None):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    if logo_url: config["logo_url"] = logo_url
    if banner_url: config["banner_url"] = banner_url
    await save_guild_config(guild)
    await interaction.response.send_message("Branding updated!", ephemeral=True)

@branding_group.command(name="clear", description="Clear branding")
@app_commands.choices(target=[
    app_commands.Choice(name="logo", value="logo"),
    app_commands.Choice(name="banner", value="banner"),
    app_commands.Choice(name="both", value="both")
])
@app_commands.checks.has_permissions(administrator=True)
async def branding_clear(interaction: discord.Interaction, target: str):
    guild = interaction.guild
    if not guild:
        return
    config = get_guild_config(guild.id)
    if target in ["logo", "both"]: config["logo_url"] = None
    if target in ["banner", "both"]: config["banner_url"] = None
    await save_guild_config(guild)
    await interaction.response.send_message("Branding cleared!", ephemeral=True)

bot.tree.add_command(branding_group)

ticket_group = app_commands.Group(name="ticket", description="Ticket actions")

@ticket_group.command(name="claim", description="Claim ticket")
@is_staff_or_admin()
async def ticket_claim(interaction: discord.Interaction):
    await handle_claim(interaction)

@ticket_group.command(name="unclaim", description="Unclaim ticket")
@is_staff_or_admin()
async def ticket_unclaim(interaction: discord.Interaction):
    await handle_unclaim(interaction)

@ticket_group.command(name="close", description="Close ticket")
async def ticket_close(interaction: discord.Interaction):
    await handle_close(interaction)

bot.tree.add_command(ticket_group)

if __name__ == "__main__":
    bot.run(TOKEN)
                                                                     
