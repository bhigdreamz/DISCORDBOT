import os
import asyncio
import discord
from discord import app_commands
import aiohttp
import json
import time
from datetime import datetime, timedelta
from discord.ext import tasks, commands
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

# ==========================================================
# CONFIG AND SETUP
# ==========================================================

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TORN_API_KEY = os.getenv("TORN_API_KEY")
FACTION_ID = int(os.getenv("YOUR_FACTION_ID", "37537"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "1360732124033847387"))
MESSAGE_CLEANUP_DELAY = 120  # Time in seconds to wait before deleting bot messages (2 minutes)

# Ensure data directories exist
DATA_DIR = "bot_data"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "wars"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "users"), exist_ok=True)
os.makedirs(os.path.join(DATA_DIR, "attacks"), exist_ok=True)

# Data file paths
USER_PREFS_FILE = os.path.join(DATA_DIR, "user_preferences.json")
WAR_HISTORY_FILE = os.path.join(DATA_DIR, "war_history.json")
CURRENT_WAR_FILE = os.path.join(DATA_DIR, "current_war.json")
ATTACK_LOGS_FILE = os.path.join(DATA_DIR, "attack_logs.json")

# Discord setup
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True  # Need this for getting member info
intents.reactions = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global state
claimed_targets = {}
previous_war_id = None  # For tracking war end
bot.claimed_targets = claimed_targets  # Make it accessible to all commands
messages_to_delete = []  # Track messages for deletion
current_war_data = {}  # Track current war data
user_preferences = {}  # Track user notification preferences
war_history = []  # Track war history
attack_logs = {}  # Track attack logs

# HTTP headers
HEADERS = {"User-Agent": "AttackAlertBot/1.0"}

# Helper URLs
TORN_PROFILE_URL = "https://www.torn.com/profiles.php?XID={}"
TORN_FACTION_URL = "https://www.torn.com/factions.php?step=profile&ID={}"
TORN_COMPANY_URL = "https://www.torn.com/companies.php?step=profile&ID={}"

# ==========================================================
# UTILITY FUNCTIONS
# ==========================================================

def format_time_difference(seconds):
    """Format a time difference in seconds into a readable string"""
    days = seconds // (24 * 3600)
    seconds %= (24 * 3600)
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60
    seconds %= 60

    result = ""
    if days > 0:
        result += f"{days}d "
    if hours > 0 or days > 0:
        result += f"{hours}h "
    if minutes > 0 or hours > 0 or days > 0:
        result += f"{minutes}m "
    result += f"{seconds}s"

    return result

# DATA MANAGEMENT FUNCTIONS
# ==========================================================

def load_data():
    """Load saved data from files"""
    global user_preferences, war_history, attack_logs, current_war_data

    # Load user preferences
    if os.path.exists(USER_PREFS_FILE):
        try:
            with open(USER_PREFS_FILE, 'r') as f:
                user_preferences = json.load(f)
        except:
            user_preferences = {}

    # Load war history
    if os.path.exists(WAR_HISTORY_FILE):
        try:
            with open(WAR_HISTORY_FILE, 'r') as f:
                war_history = json.load(f)
        except:
            war_history = []

    # Load attack logs
    if os.path.exists(ATTACK_LOGS_FILE):
        try:
            with open(ATTACK_LOGS_FILE, 'r') as f:
                attack_logs = json.load(f)
        except:
            attack_logs = {}

    # Load current war data
    if os.path.exists(CURRENT_WAR_FILE):
        try:
            with open(CURRENT_WAR_FILE, 'r') as f:
                current_war_data = json.load(f)
        except:
            current_war_data = {}

def save_user_preferences():
    """Save user notification preferences"""
    with open(USER_PREFS_FILE, 'w') as f:
        json.dump(user_preferences, f, indent=2)

def save_war_history():
    """Save war history data"""
    with open(WAR_HISTORY_FILE, 'w') as f:
        json.dump(war_history, f, indent=2)

    # Also save detailed war data to individual files
    for war in war_history:
        war_id = war.get("war_id")
        if war_id:
            war_file = os.path.join(DATA_DIR, "wars", f"war_{war_id}.json")
            with open(war_file, 'w') as f:
                json.dump(war, f, indent=2)

def save_attack_logs():
    """Save attack logs data"""
    with open(ATTACK_LOGS_FILE, 'w') as f:
        json.dump(attack_logs, f, indent=2)

def save_current_war():
    """Save current war data"""
    with open(CURRENT_WAR_FILE, 'w') as f:
        json.dump(current_war_data, f, indent=2)

def record_attack(attacker_id, defender_id, points_gained, timestamp=None):
    """Record an attack for leaderboard tracking"""
    global attack_logs

    if timestamp is None:
        timestamp = int(datetime.now().timestamp())

    # Get current war id
    war_id = current_war_data.get("war_id")
    if not war_id:
        return False

    # Initialize war in attack logs if not exists
    if war_id not in attack_logs:
        attack_logs[war_id] = {
            "attacks": [],
            "start_time": current_war_data.get("start_time", timestamp),
            "faction_id": FACTION_ID
        }

    # Add the attack (convert points to float to ensure decimal values work)
    attack_logs[war_id]["attacks"].append({
        "attacker_id": attacker_id,
        "defender_id": defender_id,
        "points": float(points_gained),
        "timestamp": timestamp
    })

    # Save the updated logs
    save_attack_logs()
    return True

def get_member_attacks(member_id, war_id=None):
    """Get all attacks made by a member in the current or specified war"""
    if war_id is None:
        war_id = current_war_data.get("war_id")

    if not war_id or war_id not in attack_logs:
        return []

    return [
        attack for attack in attack_logs[war_id]["attacks"]
        if str(attack["attacker_id"]) == str(member_id)
    ]

def get_user_stats(member_id, war_id=None):
    """Calculate user stats for a member"""
    attacks = get_member_attacks(member_id, war_id)

    if not attacks:
        return {
            "total_attacks": 0,
            "total_points": 0,
            "average_points": 0,
            "last_attacks": []
        }

    total_points = sum(attack["points"] for attack in attacks)
    last_attacks = sorted(attacks, key=lambda a: a["timestamp"], reverse=True)[:5]

    return {
        "total_attacks": len(attacks),
        "total_points": total_points,
        "average_points": total_points / len(attacks) if attacks else 0,
        "last_attacks": [attack["points"] for attack in last_attacks]
    }

def get_user_preferences(user_id):
    """Get notification preferences for a user, creating default if none exists"""
    global user_preferences

    user_id = str(user_id)  # Ensure string keys

    if user_id not in user_preferences:
        # Default preferences
        user_preferences[user_id] = {
            "notify_targets": False,
            "notify_war": False,
            "notify_chain": False,
            "last_notified": 0  # Timestamp of last notification to prevent spam
        }
        save_user_preferences()

    return user_preferences[user_id]

async def notify_users(notification_type, content, embed=None):
    """Send notifications to users who have subscribed to this type"""
    for user_id, prefs in user_preferences.items():
        preference_key = f"notify_{notification_type}"

        # Check if user wants this notification type
        if prefs.get(preference_key, False):
            # Avoid spam by checking last notification time (minimum 5 minutes between notices)
            last_notified = prefs.get("last_notified", 0)
            current_time = int(datetime.now().timestamp())

            if current_time - last_notified >= 300:  # 5 minutes
                try:
                    user = await bot.fetch_user(int(user_id))
                    if embed:
                        await user.send(content, embed=embed)
                    else:
                        await user.send(content)

                    # Update last notified time
                    prefs["last_notified"] = current_time
                    save_user_preferences()
                except Exception as e:
                    print(f"Failed to send notification to user {user_id}: {str(e)}")

# ==========================================================
# WEB SERVER FOR KEEP-ALIVE
# ==========================================================

app = Flask('')

@app.route('/')
def home():
    return "I'm alive!"

def run():
    app.run(host='0.0.0.0', port=5000)

def keep_alive():
    Thread(target=run).start()

# ==========================================================
# API AND DATA UTILITIES
# ==========================================================

async def get_json(url):
    """Get JSON from an API endpoint"""
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url) as resp:
            return await resp.json()

async def scheduled_message_delete(message, delay=MESSAGE_CLEANUP_DELAY):
    """Schedule a message for deletion after a delay"""
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except discord.NotFound:
        # Message was already deleted
        pass
    except discord.Forbidden:
        # No permission to delete
        print("Warning: No permission to delete message")
    except Exception as e:
        print(f"Error deleting message: {str(e)}")

async def get_opponent_faction():
    """Get information about the current war opponent using v2 API"""
    global current_war_data

    # Use v2 API with direct faction ID
    url = f"https://api.torn.com/v2/faction/37537/rankedwars?key={TORN_API_KEY}"
    data = await get_json(url)

    print(f"Fetching war data for faction: {target_faction}")

    # Debug: Save the raw response to a file for inspection
    with open('torn_api_response.json', 'w') as f:
        json.dump(data, f, indent=2)

    if "rankedwars" in data and data["rankedwars"]:
        # Find active war (end = 0)
        for war in data["rankedwars"]:
            if war.get("end", 1) == 0:
                war_id = str(war["id"])

                opponent_faction = None
                our_faction_data = None

                # Debug faction data structure
                print(f"Checking factions in war {war_id}")
                print(f"Our faction ID: {FACTION_ID}")

                # Get faction data with proper type handling
                factions = war.get("factions", [])
                if isinstance(factions, list):
                    for faction in factions:
                        faction_id = int(faction.get("id", 0))
                        if faction_id == FACTION_ID:
                            our_faction_data = faction
                            print(f"Found our faction: {faction.get('name', 'unknown')}")
                        else:
                            opponent_faction = faction
                elif isinstance(factions, dict):
                    # Handle case where factions might be a dictionary
                    for faction_id, faction in factions.items():
                        if int(faction_id) == FACTION_ID or int(faction.get("id", 0)) == FACTION_ID:
                            our_faction_data = faction
                        else:
                            opponent_faction = faction

                if opponent_faction:
                    print(f"ATTACK ALERT#{war_id}")
                    print(f"Opponent ID: {opponent_faction['id']}")
                    print(f"Opponent Name: {opponent_faction['name']}")
                    print(f"War ID: {war_id}")

                    # Update current war data with more comprehensive information
                    current_war_data = {
                        "war_id": war_id,
                        "opponent_id": opponent_faction["id"],
                        "opponent_name": opponent_faction["name"],
                        "start_time": war["start"],
                        "our_score": our_faction_data.get("score", 0),
                        "opponent_score": opponent_faction.get("score", 0),
                        "our_chain": our_faction_data.get("chain", 0),
                        "opponent_chain": opponent_faction.get("chain", 0),
                        "target_score": war.get("target", 6000),
                        "last_updated": int(datetime.now().timestamp())
                    }
                    save_current_war()

                    return opponent_faction["id"], war_id, war

    # No active war found
    if current_war_data:
        # War ended
        current_war_data = {}
        save_current_war()

    return None, None, None

async def get_opponent_members(faction_id):
    url = f"https://api.torn.com/faction/{faction_id}?selections=basic&key={TORN_API_KEY}"
    data = await get_json(url)
    return data.get("members", {})

async def is_attackable(status):
    state = status["state"]
    if state == "Okay":
        return True
    elif state == "Hospital":
        until = status.get("until", 0)
        return (until - int(datetime.now().timestamp())) <= 60
    return False

async def get_user_info(user_id):
    url = f"https://api.torn.com/user/{user_id}?selections=profile&key={TORN_API_KEY}"
    return await get_json(url)

# ==========================================================
# COMMAND GROUPS SETUP
# ==========================================================

# Setup command groups
class WarCommands(app_commands.Group):
    """War related commands"""

    @app_commands.command(name="status", description="Show current war status with scores")
    async def warstatus(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await show_war_status(interaction)

    @app_commands.command(name="history", description="View past war results")
    @app_commands.describe(war_id="Optional war ID to see specific details")
    async def warhistory(self, interaction: discord.Interaction, war_id: str = None):
        await interaction.response.defer(ephemeral=False)
        await show_war_history(interaction, war_id)

    @app_commands.command(name="leaderboard", description="View official member contributions")
    @app_commands.describe(war_id="Optional war ID to view (defaults to most recent completed war)")
    async def leaderboard(self, interaction: discord.Interaction, war_id: str = None):
        await interaction.response.defer(ephemeral=False)
        await show_leaderboard(interaction, war_id)

    @app_commands.command(name="result", description="View detailed war result including rewards")
    @app_commands.describe(war_id="Optional war ID to view (defaults to most recent completed war)")
    async def war_result(self, interaction: discord.Interaction, war_id: str = None):
        """View detailed war result including rank changes and rewards"""
        await interaction.response.defer(ephemeral=False)
        await show_war_result(interaction, war_id)

    @app_commands.command(name="record", description="Record an attack for leaderboard tracking")
    @app_commands.describe(
        defender_id="The ID of the player you attacked",
        points="How many points you gained from the attack (can use decimals, e.g. 2.5)"
    )
    async def record(self, interaction: discord.Interaction, defender_id: int, points: float):
        await interaction.response.defer(ephemeral=True)
        await record_attack_command(interaction, defender_id, points)

    @app_commands.command(name="delete_record", description="[ADMIN] Delete incorrect attack records")
    @app_commands.describe(
        attack_id="The ID of the attack to delete (use /war logs to see IDs)",
        war_id="War ID to delete from (default: current war)"
    )
    @app_commands.default_permissions(administrator=True)
    async def delete_record(self, interaction: discord.Interaction, attack_id: int, war_id: str = None):
        await interaction.response.defer(ephemeral=True)
        await delete_attack_record(interaction, attack_id, war_id)

    @app_commands.command(name="logs", description="View raw attack logs with IDs for admin management")
    @app_commands.describe(
        war_id="War ID to show logs for (default: current war)"
    )
    @app_commands.default_permissions(administrator=True)
    async def attack_logs(self, interaction: discord.Interaction, war_id: str = None):
        await interaction.response.defer(ephemeral=True)
        await show_attack_logs(interaction, war_id)

    @app_commands.command(name="debug", description="Debug command to show war data structure")
    async def debug_war(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await debug_war_command(interaction)

class TargetCommands(app_commands.Group):
    """Target related commands"""

    @app_commands.command(name="info", description="Get detailed info about a specific target")
    @app_commands.describe(user_id="The ID of the player to get info about")
    async def target(self, interaction: discord.Interaction, user_id: str):
        await interaction.response.defer(ephemeral=False)
        await show_target_info(interaction, user_id)

    @app_commands.command(name="claim", description="Claim a target")
    @app_commands.describe(user_id="The ID of the player to claim")
    async def claim(self, interaction: discord.Interaction, user_id: int):
        await interaction.response.defer(ephemeral=True)
        await claim_target(interaction, user_id)

    @app_commands.command(name="unclaim", description="Remove a claim on a target")
    @app_commands.describe(user_id="The ID of the player to unclaim")
    async def unclaim(self, interaction: discord.Interaction, user_id: int):
        await interaction.response.defer(ephemeral=True)
        await unclaim_target(interaction, user_id)

    @app_commands.command(name="list", description="Show all currently claimed targets")
    async def claims(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        await show_claimed_targets(interaction)

class InfoCommands(app_commands.Group):
    """Information related commands"""

    @app_commands.command(name="faction", description="Get info about a faction")
    @app_commands.describe(input_id="Faction ID or player ID to get faction info for")
    async def faction(self, interaction: discord.Interaction, input_id: str):
        await interaction.response.defer(ephemeral=False)
        await show_faction_info(interaction, input_id)

    @app_commands.command(name="company", description="Get company info about a player")
    @app_commands.describe(input_id="Player ID to get company info for")
    async def company(self, interaction: discord.Interaction, input_id: str):
        await interaction.response.defer(ephemeral=False)
        await show_company_info(interaction, input_id)

    @app_commands.command(name="mystats", description="View your contribution stats")
    @app_commands.describe(
        war_id="Optional war ID or 'all' to see all-time stats"
    )
    async def mystats(self, interaction: discord.Interaction, war_id: str = None):
        await interaction.response.defer(ephemeral=False)
        await show_my_stats(interaction, war_id)

class NotifyCommands(app_commands.Group):
    """Notification related commands"""

    @app_commands.command(name="settings", description="View or change notification settings")
    @app_commands.describe(
        notify_type="Type of notification to configure (optional)",
        setting="Turn notifications on or off (optional)"
    )
    @app_commands.choices(
        notify_type=[
            app_commands.Choice(name="targets", value="targets"),
            app_commands.Choice(name="war", value="war"),
            app_commands.Choice(name="chain", value="chain"),
            app_commands.Choice(name="all", value="all")
        ],
        setting=[
            app_commands.Choice(name="on", value="on"),
            app_commands.Choice(name="off", value="off")
        ]
    )
    async def notify(
        self, 
        interaction: discord.Interaction, 
        notify_type: str = None, 
        setting: str = None
    ):
        await interaction.response.defer(ephemeral=True)
        await manage_notifications(interaction, notify_type, setting)

# ==========================================================
# SLASH COMMAND IMPLEMENTATIONS
# ==========================================================

async def show_war_status(interaction: discord.Interaction):
    """Show the current status of the faction war with a well-formatted embed using v2 API data"""
    try:
        # Call updated get_opponent_faction() which now uses v2 API
        opponent_id, war_id, war_data = await get_opponent_faction()

        if opponent_id is None or war_data is None:
            await interaction.followup.send("⚠️ No ongoing ranked war.")
            return

        # Get direct data from current_war_data which has been updated with v2 API
        our_faction_name = "Our Faction"  # Default
        opponent_faction_name = current_war_data.get("opponent_name", "Opponent")

        our_score = current_war_data.get("our_score", 0)
        opponent_score = current_war_data.get("opponent_score", 0)

        our_chain = current_war_data.get("our_chain", 0)
        opponent_chain = current_war_data.get("opponent_chain", 0)

        target_score = current_war_data.get("target_score", 6000)

        # Get our faction name if missing
        if "our_faction_name" not in current_war_data:
            try:
                # Fetch our faction info to get name
                url = f"https://api.torn.com/faction/{FACTION_ID}?selections=basic&key={TORN_API_KEY}"
                faction_data = await get_json(url)
                our_faction_name = faction_data.get("name", "Our Faction")
                current_war_data["our_faction_name"] = our_faction_name
                save_current_war()
            except:
                # Use default name if API call fails
                our_faction_name = "Our Faction"
        else:
            our_faction_name = current_war_data["our_faction_name"]

        # Calculate lead
        lead = our_score - opponent_score
        lead_text = f"{abs(lead):,}"
        lead_direction = "LEAD" if lead >= 0 else "BEHIND"

        # Calculate progress
        max_score = max(our_score, opponent_score)
        progress_percentage = (max_score / target_score) * 100 if target_score > 0 else 0
        progress = f"{max_score:,} / {target_score:,}"

        # Format time remaining if possible
        time_remaining = ""
        start_time = war_data.get("war", {}).get("start", 0)
        if start_time > 0:
            # Wars typically last 5 days
            end_time = start_time + (5 * 24 * 60 * 60)
            now = int(datetime.now().timestamp())

            if end_time > now:
                seconds_left = end_time - now
                days = seconds_left // (24 * 3600)
                seconds_left %= (24 * 3600)
                hours = seconds_left // 3600
                seconds_left %= 3600
                minutes = seconds_left // 60
                seconds = seconds_left % 60

                time_remaining = f"{days:02d}:{hours:02d}:{minutes:02d}:{seconds:02d}"

        # Create faction links
        our_faction_link = f"[{our_faction_name}]({TORN_FACTION_URL.format(FACTION_ID)})"
        opponent_faction_link = f"[{opponent_faction_name}]({TORN_FACTION_URL.format(opponent_id)})"

        # Create a nicely formatted embed with real-time API data
        embed = discord.Embed(
            title="⚔️ Current War Status (Real-Time)",
            description=f"{our_faction_link} vs {opponent_faction_link}",
            color=0x1abc9c if lead >= 0 else 0xe74c3c  # Green if leading, red if behind
        )

        # Add scores with highlighting for who's ahead
        our_score_field = f"**{our_score:,}** 🔥" if lead >= 0 else f"{our_score:,}"
        opponent_score_field = f"{opponent_score:,}" if lead >= 0 else f"**{opponent_score:,}** 🔥"

        embed.add_field(name=our_faction_name, value=our_score_field, inline=True)

        # Add a "vs" field in the middle
        lead_field_title = "LEAD" if lead >= 0 else "BEHIND"
        lead_field_value = f"**{lead_text}**"
        embed.add_field(name=lead_field_title, value=lead_field_value, inline=True)

        embed.add_field(name=opponent_faction_name, value=opponent_score_field, inline=True)

        # Progress information
        progress_field = f"{progress}\n{progress_percentage:.1f}% Complete"
        embed.add_field(name="Progress", value=progress_field, inline=False)

        # Chain information from API v2
        our_chain = current_war_data.get("our_chain", 0)
        opponent_chain = current_war_data.get("opponent_chain", 0)

        if our_chain > 0 or opponent_chain > 0:
            chain_field = f"{our_faction_name}: **{our_chain}**\n{opponent_faction_name}: **{opponent_chain}**"
            embed.add_field(name="Current Chains", value=chain_field, inline=True)

        # Time information
        if time_remaining:
            elapsed_field = f"War started <t:{current_war_data.get('start_time', 0)}:R>"
            embed.add_field(name="Elapsed Time", value=elapsed_field, inline=True)

        # Add war ID with link to Torn
        war_link = f"[#{war_id}](https://www.torn.com/factions.php?step=profile&ID={FACTION_ID}#/tab=tab5)"
        embed.add_field(name="War ID", value=war_link, inline=True)

        # Set footer with timestamp
        embed.set_footer(text=f"Data from Torn API v2 | Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # Add View War button
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="View War Page",
                url=f"https://www.torn.com/factions.php?step=profile&ID={FACTION_ID}#/tab=tab5",
                style=discord.ButtonStyle.link
            )
        )

        # War status messages should stay visible - no auto-delete
        await interaction.followup.send(embed=embed, view=view)
    except Exception as e:
        await interaction.followup.send(f"❌ Error checking war status: {str(e)}")
        # Add more detailed error reporting
        import traceback
        error_details = traceback.format_exc()
        print(f"Error in warstatus command: {error_details}")

async def show_target_info(interaction: discord.Interaction, user_id: str):
    """Get detailed info about a specific target"""
    try:
        if not user_id:
            await interaction.followup.send("❌ Please provide a target ID.")
            return

        # Clean up the user_id string and convert to int
        user_id = int(''.join(filter(str.isdigit, user_id)))
        user_data = await get_user_info(user_id)
        name = user_data.get("name", "Unknown")
        level = user_data.get("level", "N/A")
        last_active = user_data.get("last_action", {}).get("relative", "Unknown")
        status = user_data.get("status", {}).get("state", "Unknown")
        faction_info = user_data.get("faction", {})

        embed = discord.Embed(
            title=f"Target Information",
            description=f"**[{name} ({user_id})]({TORN_PROFILE_URL.format(user_id)})**",
            color=0x1abc9c
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Level", value=level, inline=True)
        embed.add_field(name="Last Active", value=last_active, inline=True)

        # Make faction link clickable if available
        if faction_info:
            faction_id = faction_info.get("faction_id")
            faction_name = faction_info.get("faction_name", "None")
            if faction_id:
                faction_value = f"[{faction_name}]({TORN_FACTION_URL.format(faction_id)})"
            else:
                faction_value = faction_name
            embed.add_field(name="Faction", value=faction_value, inline=True)

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Attack",
                url=f"https://www.torn.com/loader.php?sid=attack&user2ID={user_id}",
                style=discord.ButtonStyle.link
            )
        )

        response_msg = await interaction.followup.send(embed=embed, view=view)
        asyncio.create_task(scheduled_message_delete(response_msg))
    except Exception as e:
        await interaction.followup.send(f"Error getting target info: {str(e)}")

async def claim_target(interaction: discord.Interaction, user_id: int):
    """Claim a target"""
    try:
        claimed_targets[user_id] = interaction.user.id
        await interaction.followup.send(f"Target {user_id} claimed by {interaction.user.display_name}", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error claiming target: {str(e)}", ephemeral=True)

async def unclaim_target(interaction: discord.Interaction, user_id: int):
    """Remove a claim on a target"""
    try:
        if user_id in claimed_targets:
            del claimed_targets[user_id]
            await interaction.followup.send(f"Unclaimed target {user_id}", ephemeral=True)
        else:
            await interaction.followup.send("This target was not claimed.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error unclaiming target: {str(e)}", ephemeral=True)

async def show_claimed_targets(interaction: discord.Interaction):
    """Show all currently claimed targets"""
    try:
        if not claimed_targets:
            await interaction.followup.send("No targets are currently claimed.")
            return

        embed = discord.Embed(title="Currently Claimed Targets", color=0x1abc9c)
        for target_id, claimer_id in claimed_targets.items():
            claimer = interaction.guild.get_member(claimer_id)
            claimer_name = claimer.display_name if claimer else "Unknown"

            # Get target info if possible
            try:
                user_data = await get_user_info(target_id)
                name = user_data.get("name", f"User {target_id}")
                target_field = f"[{name} ({target_id})]({TORN_PROFILE_URL.format(target_id)})"
            except:
                target_field = f"Target ID: {target_id}"

            embed.add_field(
                name=target_field,
                value=f"Claimed by: {claimer_name}",
                inline=False
            )

        response_msg = await interaction.followup.send(embed=embed)
        asyncio.create_task(scheduled_message_delete(response_msg))
    except Exception as e:
        await interaction.followup.send(f"Error showing claimed targets: {str(e)}")

async def show_faction_info(interaction: discord.Interaction, input_id: str):
    """Get information about a faction by ID or member ID"""
    try:
        if not input_id:
            await interaction.followup.send("❌ Please provide an ID.")
            return

        # Clean up the input ID and convert to int
        input_id = int(''.join(filter(str.isdigit, input_id)))

        # First try to get user's faction if it's a user ID
        user_data = await get_json(f"https://api.torn.com/user/{input_id}?selections=profile&key={TORN_API_KEY}")
        faction_id = user_data.get("faction", {}).get("faction_id")

        if not faction_id:
            # If no faction found in user data, treat input as faction ID
            faction_id = input_id

        faction_data = await get_json(f"https://api.torn.com/faction/{faction_id}?selections=basic&key={TORN_API_KEY}")

        name = faction_data.get("name", "Unknown")
        respect = faction_data.get("respect", 0)
        members_count = len(faction_data.get("members", {}))
        leader_id = faction_data.get("leader", 0)

        # Get leader's info
        leader_data = await get_json(f"https://api.torn.com/user/{leader_id}?selections=profile&key={TORN_API_KEY}")
        leader_name = leader_data.get("name", "Unknown")

        # Create clickable links for faction and leader
        faction_link = f"[{name}]({TORN_FACTION_URL.format(faction_id)})"
        leader_link = f"[{leader_name} ({leader_id})]({TORN_PROFILE_URL.format(leader_id)})"

        embed = discord.Embed(
            title="Faction Information",
            description=f"**{faction_link}**",
            color=0x1abc9c
        )
        embed.add_field(name="Leader", value=leader_link, inline=True)
        embed.add_field(name="Members", value=members_count, inline=True)
        embed.add_field(name="Respect", value=f"{respect:,}", inline=True)

        # Add some additional faction info if available
        if "capacity" in faction_data:
            embed.add_field(name="Capacity", value=f"{members_count}/{faction_data['capacity']}", inline=True)
        if "age" in faction_data:
            embed.add_field(name="Age", value=f"{faction_data['age']} days", inline=True)

        response_msg = await interaction.followup.send(embed=embed)
        asyncio.create_task(scheduled_message_delete(response_msg))
    except Exception as e:
        await interaction.followup.send(f"Error getting faction info: {str(e)}")

async def show_company_info(interaction: discord.Interaction, input_id: str):
    """Get company info about a player or company"""
    try:
        if not input_id:
            await interaction.followup.send("❌ Please provide an ID.")
            return

        # Clean up the input ID and convert to int
        input_id = int(''.join(filter(str.isdigit, input_id)))

        # First try as user ID to get their company
        user_data = await get_json(f"https://api.torn.com/user/{input_id}?selections=profile&key={TORN_API_KEY}")

        job = user_data.get("job", {})
        company_id = job.get("company_id")
        company_name = job.get("company_name", "Unemployed")
        company_position = job.get("position", "N/A")

        if company_id:
            # Try to get more company details
            try:
                company_data = await get_json(f"https://api.torn.com/company/{company_id}?selections=profile&key={TORN_API_KEY}")
                # Add any additional company info here if needed
            except:
                company_data = {}

            # Create clickable company link
            company_link = f"[{company_name}]({TORN_COMPANY_URL.format(company_id)})"

            embed = discord.Embed(
                title="Company Information",
                description=f"**[{user_data.get('name')}]({TORN_PROFILE_URL.format(input_id)})**",
                color=0x1abc9c
            )
            embed.add_field(name="Company", value=company_link, inline=True)
            embed.add_field(name="Position", value=company_position, inline=True)

            # Add company details if available
            if company_data:
                if "type" in company_data:
                    embed.add_field(name="Type", value=company_data.get("type", "Unknown"), inline=True)
                if "rating" in company_data:
                    embed.add_field(name="Rating", value=f"{company_data.get('rating', 0)}/10", inline=True)
        else:
            # No company, show basic info
            embed = discord.Embed(
                title="Company Information",
                description=f"**[{user_data.get('name')}]({TORN_PROFILE_URL.format(input_id)})**",
                color=0x1abc9c
            )
            embed.add_field(name="Status", value="Unemployed", inline=True)

        response_msg = await interaction.followup.send(embed=embed)
        asyncio.create_task(scheduled_message_delete(response_msg))
    except Exception as e:
        await interaction.followup.send(f"Error getting company info: {str(e)}")

async def show_war_history(interaction: discord.Interaction, war_id: str = None):
    """View war history using the v2 API endpoints

    This has been updated to handle both v2 and v1 API formats and uses the
    /v2/faction/rankedwars endpoint to show all wars for the faction in the .env file
    """
    try:
        await interaction.followup.send("Fetching war history from Torn API...", ephemeral=True)

        # If a specific war ID is requested
        if war_id:
            # Try to get the war from the v2 API first
            try:
                # Use the endpoint that gets all wars for our faction
                url = f"https://api.torn.com/v2/faction/rankedwars?key={TORN_API_KEY}"
                api_data = await get_json(url)

                api_war_data = None
                if "rankedwars" in api_data:
                    for war in api_data["rankedwars"]:
                        if str(war["id"]) == str(war_id):
                            api_war_data = war
                            break

                if api_war_data:
                    # Process data from the v2 API
                    start_time = datetime.fromtimestamp(api_war_data.get("start", 0)).strftime('%b %d, %Y')

                    our_data = None
                    opponent_data = None

                    # Get faction details - handle both list and dict format
                    factions = api_war_data.get("factions", [])
                    if isinstance(factions, list):
                        for faction in factions:
                            if int(faction.get("id", 0)) == FACTION_ID:
                                our_data = faction
                            else:
                                opponent_data = faction
                    elif isinstance(factions, dict):
                        # Handle case where factions might be a dictionary
                        for faction_id, faction in factions.items():
                            faction_id_int = int(faction_id) if faction_id.isdigit() else 0
                            if faction_id_int == FACTION_ID or int(faction.get("id", 0)) == FACTION_ID:
                                our_data = faction
                            else:
                                opponent_data = faction

                    if not our_data or not opponent_data:
                        await interaction.followup.send(f"❌ Incomplete data for war {war_id}.", ephemeral=True)
                        return

                    our_name = our_data.get("name", "Our Faction")
                    opponent_name = opponent_data.get("name", "Opponent")
                    opponent_id = opponent_data.get("id")

                    # Create clickable faction links
                    our_faction_link = f"[{our_name}](https://www.torn.com/factions.php?step=profile&ID={FACTION_ID})"
                    opponent_faction_link = f"[{opponent_name}](https://www.torn.com/factions.php?step=profile&ID={opponent_id})"

                    our_score = our_data.get("score", 0)
                    opponent_score = opponent_data.get("score", 0)

                    # Check if war has ended
                    if api_war_data.get("end", 0) > 0:
                        end_time = datetime.fromtimestamp(api_war_data.get("end", 0)).strftime('%b %d, %Y')
                        we_won = our_score > opponent_score
                        status = "WON" if we_won else "LOST"
                        color = 0x1abc9c if we_won else 0xe74c3c
                    else:
                        end_time = "In Progress"
                        lead = our_score - opponent_score
                        if lead > 0:
                            status = f"LEADING by {abs(lead):,}"
                            color = 0x1abc9c
                        elif lead < 0:
                            status = f"BEHIND by {abs(lead):,}"
                            color = 0xe74c3c
                        else:
                            status = "TIED"
                            color = 0x3498db

                    # Calculate war duration
                    if api_war_data.get("end", 0) > 0:
                        duration = format_time_difference(api_war_data["end"] - api_war_data["start"])
                    else:
                        duration = format_time_difference(int(datetime.now().timestamp()) - api_war_data["start"])

                    # Create the embed with v2 API data
                    embed = discord.Embed(
                        title=f"War #{war_id} Details",
                        description=f"**{our_faction_link}** ({our_score:,}) vs **{opponent_faction_link}** ({opponent_score:,})",
                        color=color
                    )

                    embed.add_field(name="Start", value=start_time, inline=True)
                    embed.add_field(name="End", value=end_time, inline=True)
                    embed.add_field(name="Status", value=status, inline=True)

                    # Add target and progress
                    target = api_war_data.get("target", 5000)
                    progress = max(our_score, opponent_score) / target * 100

                    embed.add_field(name="Target", value=f"{target:,}", inline=True)
                    embed.add_field(name="Progress", value=f"{progress:.1f}%", inline=True)
                    embed.add_field(name="Duration", value=duration, inline=True)

                    # Add chain information if available
                    our_chain = our_data.get("chain", 0)
                    opponent_chain = opponent_data.get("chain", 0)

                    if our_chain > 0 or opponent_chain > 0:
                        chain_text = f"Our Chain: {our_chain}\n"
                        chain_text += f"Opponent Chain: {opponent_chain}"
                        embed.add_field(name="Chains", value=chain_text, inline=False)

                    # Add manual attack stats if available
                    if war_id in attack_logs:
                        war_attacks = attack_logs.get(war_id, {}).get("attacks", [])
                        if war_attacks:
                            total_attacks = len(war_attacks)
                            total_points = sum(attack["points"] for attack in war_attacks)
                            avg_points = total_points / total_attacks if total_attacks else 0

                            stats_text = f"Total Recorded Attacks: {total_attacks}\n"
                            stats_text += f"Total Recorded Points: {total_points:.1f}\n"
                            stats_text += f"Avg Points/Attack: {avg_points:.1f}"

                            embed.add_field(name="Manual Attack Stats", value=stats_text, inline=False)

                    await interaction.followup.send(embed=embed)

                    # Show attack leaderboard if we have manual data
                    if war_id in attack_logs and attack_logs[war_id]["attacks"]:
                        await show_leaderboard(interaction, war_id)

                else:
                    # API didn't have the war, try local history
                    war_data = None
                    for war in war_history:
                        if str(war.get("war_id")) == str(war_id):
                            war_data = war
                            break

                    if not war_data:
                        await interaction.followup.send(f"❌ War ID {war_id} not found in API or local history.", ephemeral=True)
                        return

                    # Use the original local history format
                    start_time = datetime.fromtimestamp(war_data.get("start_time", 0)).strftime('%b %d, %Y')
                    end_time = datetime.fromtimestamp(war_data.get("end_time", 0)).strftime('%b %d, %Y')

                    our_faction = war_data.get("faction_data", {}).get(str(FACTION_ID), {})
                    opponent_faction = None
                    opponent_id = None
                    for fid, fdata in war_data.get("faction_data", {}).items():
                        if fid != str(FACTION_ID):
                            opponent_faction = fdata
                            opponent_id = fid
                            break

                    # Create local history display
                    # (rest of original logic)
                    if not opponent_faction:
                        await interaction.followup.send(f"❌ Incomplete data for war {war_id}.", ephemeral=True)
                        return

                    our_name = our_faction.get("name", "Our Faction")
                    opponent_name = opponent_faction.get("name", "Opponent")

                    # Create clickable faction links
                    our_faction_link = f"[{our_name}](https://www.torn.com/factions.php?step=profile&ID={FACTION_ID})"
                    opponent_faction_link = f"[{opponent_name}](https://www.torn.com/factions.php?step=profile&ID={opponent_id})"

                    our_score = our_faction.get("final_score", 0)
                    opponent_score = opponent_faction.get("final_score", 0)

                    we_won = war_data.get("winner") == str(FACTION_ID)
                    outcome = "WON" if we_won else "LOST"

                    embed = discord.Embed(
                        title=f"War #{war_id} Details (Local History)",
                        description=f"**{our_faction_link}** ({our_score}) vs **{opponent_faction_link}** ({opponent_score})",
                        color=0x1abc9c if we_won else 0xe74c3c
                    )

                    embed.add_field(name="Start", value=start_time, inline=True)
                    embed.add_field(name="End", value=end_time, inline=True)
                    embed.add_field(name="Outcome", value=outcome, inline=True)

                    # Add some stats if available
                    if war_id in attack_logs:
                        war_attacks = attack_logs.get(war_id, {}).get("attacks", [])
                        if war_attacks:
                            total_attacks = len(war_attacks)
                            total_points = sum(attack["points"] for attack in war_attacks)
                            avg_points = total_points / total_attacks if total_attacks else 0

                            stats_text = f"Total Attacks: {total_attacks}\n"
                            stats_text += f"Total Points: {total_points}\n"
                            stats_text += f"Avg Points/Attack: {avg_points:.1f}"

                            embed.add_field(name="Attack Stats", value=stats_text, inline=False)

                    await interaction.followup.send(embed=embed)

                    # Show leaderboard if available
                    if war_id in attack_logs and attack_logs[war_id]["attacks"]:
                        await show_leaderboard(interaction, war_id)

            except Exception as e:
                await interaction.followup.send(f"Error fetching war from API: {str(e)}", ephemeral=True)

                # Fall back to old code
                war_data = None
                for war in war_history:
                    if str(war.get("war_id")) == str(war_id):
                        war_data = war
                        break

                if not war_data:
                    await interaction.followup.send(f"❌ War ID {war_id} not found in history.", ephemeral=True)
                    return

                # Create detailed embed for this war (fallback code)
                start_time = datetime.fromtimestamp(war_data.get("start_time", 0)).strftime('%b %d, %Y')
                end_time = datetime.fromtimestamp(war_data.get("end_time", 0)).strftime('%b %d, %Y')

                # (rest of original code for displaying from local history)

        else:
            # Show list of all wars from the v2 API
            try:
                current_year = datetime.now().year
                url = f"https://api.torn.com/v2/faction/rankedwars?from={current_year-1}&to={current_year+1}&sort=DESC&key={TORN_API_KEY}"
                api_data = await get_json(url)

                # Debug: Save the raw response for troubleshooting
                with open('war_history_all_response.json', 'w') as f:
                    json.dump(api_data, f, indent=2)

                print("Fetched war history list data from API")
                print(f"API response keys: {list(api_data.keys())}")

                if "error" in api_data:
                    print(f"API Error: {api_data['error']}")
                    await interaction.followup.send(f"API Error: {api_data['error']}. Checking local history.", ephemeral=True)

                    # Fallback to local history code
                    if not war_history:
                        await interaction.followup.send("No past wars found in history either.", ephemeral=True)
                        return

                    # (original code for displaying from local history)
                    return

                if "rankedwars" not in api_data or not api_data["rankedwars"]:
                    print("No rankedwars found in API response")
                    await interaction.followup.send("No wars found in API. Checking local history.", ephemeral=True)

                    if not war_history:
                        await interaction.followup.send("No past wars found in history either.", ephemeral=True)
                        return

                    # Fallback to original local history code
                    # (original code for displaying from local history)
                    return

                print(f"Found {len(api_data['rankedwars'])} wars in API response")

                # Process wars from the v2 API
                embed = discord.Embed(
                    title="War History",
                    description="Recent wars from Torn API (newest first):",
                    color=0x1abc9c
                )

                # Show up to 12 wars
                war_count = 0
                for war in api_data["rankedwars"]:
                    if war_count >= 12:
                        break

                    war_id = str(war["id"])
                    start_date = datetime.fromtimestamp(war["start"]).strftime('%b %d')

                    # Find our faction and opponent faction - handle different formats
                    our_data = None
                    opponent_data = None

                    # Handle different faction data formats based on API test
                    factions = war.get("factions", [])

                    print(f"Processing war {war.get('id')} factions data type: {type(factions)}")

                    if isinstance(factions, list):
                        # List format (confirmed from API test)
                        for faction in factions:
                            faction_id = int(faction.get("id", 0))
                            if faction_id == FACTION_ID:
                                our_data = faction
                                print(f"Found our faction in list data: {faction.get('name', 'unknown')}")
                            else:
                                opponent_data = faction
                                print(f"Found opponent faction in list data: {faction.get('name', 'unknown')}")
                    elif isinstance(factions, dict):
                        # Dictionary format (alternative format)
                        for faction_id, faction in factions.items():
                            faction_id_int = int(faction_id) if faction_id.isdigit() else 0
                            if faction_id_int == FACTION_ID or int(faction.get("id", 0)) == FACTION_ID:
                                our_data = faction
                                print(f"Found our faction in dict data: {faction.get('name', 'unknown')}")
                            else:
                                opponent_data = faction
                                print(f"Found opponent faction in dict data: {faction.get('name', 'unknown')}")

                    if not our_data:
                        print(f"WARNING: Could not find our faction (ID: {FACTION_ID}) in war {war.get('id')}")
                    if not opponent_data:
                        print(f"WARNING: Could not find opponent faction in war {war.get('id')}")

                    if not our_data or not opponent_data:
                        continue

                    our_score = our_data.get("score", 0)
                    opponent_score = opponent_data.get("score", 0)
                    opponent_name = opponent_data.get("name", "Opponent")
                    opponent_id = opponent_data.get("id")

                    # Create clickable opponent link
                    opponent_link = f"[{opponent_name}](https://www.torn.com/factions.php?step=profile&ID={opponent_id})"

                    # Determine outcome
                    if war.get("end", 0) > 0:
                        we_won = our_score > opponent_score
                        outcome = "WON" if we_won else "LOST"
                    else:
                        outcome = "IN PROGRESS"

                    value = f"vs {opponent_link} ({start_date})\n"
                    value += f"Score: {our_score:,} - {opponent_score:,}\n"
                    value += f"Status: {outcome}"

                    embed.add_field(
                        name=f"War #{war_id}",
                        value=value,
                        inline=True
                    )

                    war_count += 1

                embed.set_footer(text="Use /war history war_id:<ID> for details on a specific war")
                await interaction.followup.send(embed=embed)

            except Exception as e:
                await interaction.followup.send(f"Error fetching war history from API: {str(e)}", ephemeral=True)

                # Fallback to local history if API fails
                if not war_history:
                    await interaction.followup.send("No past wars found in local history either.", ephemeral=True)
                    return

                # Sort wars by start time (newest first)
                sorted_wars = sorted(war_history, key=lambda w: w.get("end_time", 0), reverse=True)

                embed = discord.Embed(
                    title="War History (Local)",
                    description="Recent wars from local history (newest first):",
                    color=0x1abc9c
                )

                # Show the 10 most recent wars
                for i, war in enumerate(sorted_wars[:10]):
                    war_id = war.get("war_id")
                    start = datetime.fromtimestamp(war.get("start_time", 0)).strftime('%b %d')

                    opponent_id = None
                    opponent_name = None
                    for fid, fdata in war.get("faction_data", {}).items():
                        if fid != str(FACTION_ID):
                            opponent_name = fdata.get("name", "Unknown")
                            opponent_id = fid
                            break

                    if not opponent_name:
                        continue

                    # Create clickable opponent link
                    opponent_link = f"[{opponent_name}](https://www.torn.com/factions.php?step=profile&ID={opponent_id})"

                    we_won = war.get("winner") == str(FACTION_ID)
                    outcome = "WON" if we_won else "LOST"

                    embed.add_field(
                        name=f"War #{war_id}",
                        value=f"vs {opponent_link} ({start}) - {outcome}",
                        inline=False
                    )

                embed.set_footer(text="Use /war history war_id:<ID> for details on a specific war")
                await interaction.followup.send(embed=embed)

    except Exception as e:
        await interaction.followup.send(f"Error showing war history: {str(e)}", ephemeral=True)

async def fetch_attacks_from_api_v1(war_start_time, opponent_id=None):
    """Fallback to v1 API if v2 is not available

    The v1 API has different structure and limitations compared to v2
    """
    try:
        # Get the opponent faction ID if not provided
        if not opponent_id:
            opponent_id = current_war_data.get("opponent_id")
            if not opponent_id:
                print("No opponent faction ID available")
                return []

        # V1 API endpoint for attacks
        url = f"https://api.torn.com/user/?selections=attacks&key={TORN_API_KEY}"
        print("Using v1 API as fallback")

        data = await get_json(url)

        if "attacks" not in data:
            print("No attacks found in v1 API response")
            return []

        # Filter by time and opponent faction
        war_attacks = []
        war_start_datetime = datetime.fromtimestamp(war_start_time)

        for attack_id, attack in data["attacks"].items():
            # Get timestamp of attack
            ts = attack.get("timestamp_started")
            if not ts:
                continue

            attack_time = datetime.fromtimestamp(ts)

            # Skip attacks before war started
            if attack_time < war_start_datetime:
                continue

            # Check if defender is in opponent faction
            defender_faction = attack.get("defender_faction")
            if defender_faction and str(defender_faction) == str(opponent_id):
                # Build attack object similar to v2 format
                points = calculate_attack_points(attack)

                war_attacks.append({
                    "attacker_id": str(attack.get("attacker_id", "")),
                    "defender_id": str(attack.get("defender_id", "")),
                    "points": points,
                    "timestamp": ts,
                    "respect": attack.get("respect_gain", 0),
                    "result": attack.get("result", "")
                })

        return war_attacks
    except Exception as e:
        print(f"Error in v1 API fallback: {str(e)}")
        return []

def calculate_attack_points(attack):
    """Calculate points based on actual data from the attack

    Instead of inventing point values, we use actual data from the API:
    1. Use respect gained if available
    2. Otherwise count successful attacks as 1 point each
    """
    # Check API format (v1 vs v2) and extract respect and result accordingly
    respect = 0
    result = ""

    # Debug 
    print(f"Calculating points for attack: {attack}")

    # V2 API format (from our API test)
    if "respect_gain" in attack:
        respect = attack.get("respect_gain", 0)
    elif "respect" in attack:
        # Alternative format where it might be just "respect" 
        respect = attack.get("respect", 0)

    # V2 API format has result key
    if "result" in attack:
        if isinstance(attack["result"], dict) and "respect" in attack["result"]:
            # If result has a nested respect value
            respect = attack["result"]["respect"]
            result = "Success"  # Default success if we have respect
        else:
            # Direct result value
            result = attack.get("result", "")

    # Debug
    print(f"Attack respect: {respect}, result: {result}")

    # If the attack was successful and we have respect data, use that
    if respect > 0:
        print(f"Using respect value: {respect}")
        return respect

    # Otherwise, simple counting system: 1 point for successful attacks, 0 for failed
    successful_results = ["Mugged", "Hospitalized", "Attacked", "Stalemate", "Assist", "Success"]
    if result in successful_results:
        print(f"Using default 1.0 point for successful attack: {result}")
        return 1.0  # Successful attack
    else:
        print(f"Attack failed (no points): {result}")
        return 0.0  # Failed attack (Lost, Escape, Timeout, etc.)

async def fetch_war_leaderboard_data(war_id=None):
    """Fetch detailed war contribution data from the rankedwarreport endpoint

    This endpoint provides per-member scores for completed wars, and is the most
    accurate source of contribution data for completed wars.

    Args:
        war_id: Optional. The war ID to fetch data for. If None, gets the most recent completed war.

    Returns:
        A tuple with (contributors list, is_official, war_data) where:
        - contributors: list of member contribution data or None if not found/not completed
        - is_official: boolean indicating if this is official API data
        - war_data: complete war data including rewards, timestamps, etc.
    """
    try:
        # Use the rankedwarreport endpoint which provides official member scores for completed wars
        url = f"https://api.torn.com/v2/faction/{FACTION_ID}/rankedwarreport?key={TORN_API_KEY}"
        api_data = await get_json(url)

        # Debug the response
        if war_id:
            print(f"Fetching rankedwarreport data for specific war ID {war_id}")
        else:
            print("Fetching rankedwarreport data for most recent completed war")

        if "rankedwarreport" in api_data:
            report_data = api_data["rankedwarreport"]

            # If no specific war ID requested, use the war from the report (most recent completed)
            if not war_id:
                print(f"Using most recent completed war: {report_data.get('id')}")
                war_id = str(report_data.get('id'))

            # Verify this is the correct war we're looking for
            if str(report_data.get("id")) == str(war_id):
                print(f"Found matching war data in rankedwarreport for war ID {war_id}")

                # Find our faction in the data
                our_faction_data = None
                for faction in report_data.get("factions", []):
                    if int(faction.get("id", 0)) == FACTION_ID:
                        our_faction_data = faction
                        break

                if our_faction_data and "members" in our_faction_data:
                    # Process member data into the format we need
                    contributors = {}
                    for member in our_faction_data["members"]:
                        member_id = str(member["id"])
                        contributors[member_id] = {
                            "id": member_id,
                            "name": member["name"],
                            "attacks": member["attacks"],
                            "points": member["score"],
                            "level": member["level"]
                        }

                    # Sort contributors by points
                    sorted_contributors = sorted(contributors.values(), key=lambda x: x["points"], reverse=True)
                    return sorted_contributors, True, report_data  # Return full report data for war_result command
            else:
                print(f"War ID mismatch in rankedwarreport: got {report_data.get('id')}, expected {war_id}")

        # If we get here, either the war isn't completed yet or something else went wrong
        return None, False, None

    except Exception as e:
        print(f"Error fetching rankedwarreport data: {str(e)}")
        return None, False, None

async def fetch_attacks_from_api(war_start_time):
    """Fetch attacks from the Torn API attacksfull endpoint

    This function uses API v2 by default
    """
    try:
        # Calculate proper time range
        current_time = int(datetime.now().timestamp())

        # For v2 endpoint, we use the from parameter (seconds since epoch)
        url = f"https://api.torn.com/v2/user/attacksfull?limit=1000&from={war_start_time}&key={TORN_API_KEY}"

        print(f"Fetching attacks from {datetime.fromtimestamp(war_start_time).strftime('%Y-%m-%d %H:%M')} to now")
        data = await get_json(url)

        # Save the API response for debugging
        with open('api_attacks_response.json', 'w') as f:
            json.dump(data, f, indent=2)

        if "attacks" not in data:
            print("No attacks found in API response")
            if "error" in data:
                print(f"API Error: {data['error']}")
            return []

        # Debug: How many attacks total?
        total_attacks = len(data["attacks"])
        print(f"Found {total_attacks} total attacks in API response")

        # Filter attacks to only include those against the opponent faction
        opponent_id = current_war_data.get("opponent_id")
        if not opponent_id:
            print("No opponent faction ID found")
            return []

        print(f"Filtering for attacks against opponent faction: {opponent_id}")

        war_attacks = []

        # Handle different data structures for attacks
        if isinstance(data["attacks"], dict):
            # Dictionary format (v1 API)
            attack_items = data["attacks"].items()
        else:
            # List format (v2 API based on our tests)
            attack_items = [(str(i), attack) for i, attack in enumerate(data["attacks"])]

        for attack_id, attack in attack_items:
            # Print samples of the attack structure for debugging
            if attack_id == '0':  # Just log the first one as a sample
                print(f"Sample attack structure: {attack}")
                print(f"Attack keys: {list(attack.keys())}")

            # Check if the defender is in the opponent faction
            # Handle both v1 and v2 API formats
            defender_faction = None
            defender_id = None

            # V2 format (confirmed from our API test)
            if "defender" in attack and isinstance(attack["defender"], dict):
                defender = attack["defender"]
                if "faction" in defender:
                    if isinstance(defender["faction"], dict) and "id" in defender["faction"]:
                        defender_faction = defender["faction"]["id"]
                    elif isinstance(defender["faction"], int):
                        defender_faction = defender["faction"]

                if "id" in defender:
                    defender_id = defender["id"]

            # V1 format (previous implementation, keep as fallback)
            elif "defender_faction" in attack:
                defender_faction = attack.get("defender_faction")
                defender_id = attack.get("defender_id")

            # Debug
            if defender_faction and str(defender_faction) == str(opponent_id):
                print(f"Found relevant attack: {attack_id} against opponent faction {defender_faction}")

            # Process only attacks against the opponent faction
            if defender_faction and str(defender_faction) == str(opponent_id):
                # Get attacker details - handle both formats
                attacker_id = None

                # V2 format
                if "attacker" in attack and isinstance(attack["attacker"], dict):
                    attacker = attack["attacker"]
                    if "id" in attacker:
                        attacker_id = attacker["id"]

                # V1 format (fallback)
                elif "attacker_id" in attack:
                    attacker_id = attack.get("attacker_id")

                # Skip if we can't determine the attacker
                if not attacker_id:
                    continue

                # Skip if we can't determine the defender
                if not defender_id:
                    continue

                # Calculate points using the consistent calculation function
                points = calculate_attack_points(attack)

                # Get respect and result from the attack data
                respect = attack.get("respect_gain", 0)
                result = attack.get("result", "")

                # Get timestamp - handle both API formats
                timestamp = int(datetime.now().timestamp())  # Default to now

                # V2 API format uses "started" instead of "timestamp_started"
                if "started" in attack:
                    timestamp = attack.get("started")
                # Fallback to V1 format
                elif "timestamp_started" in attack:
                    timestamp = attack.get("timestamp_started")

                # Add to our list
                war_attacks.append({
                    "attacker_id": str(attacker_id),
                    "defender_id": str(defender_id),
                    "points": points,
                    "timestamp": timestamp,
                    "respect": respect,
                    "result": result
                })

        return war_attacks
    except Exception as e:
        print(f"Error fetching attacks from API: {str(e)}")
        return []

async def show_leaderboard(interaction: discord.Interaction, war_id: str = None, page: int = 1):
    """View faction contributors using official API data with pagination

    For completed wars, uses the /v2/faction/{ID}/rankedwarreport endpoint to get official
    member scores. For ongoing wars, shows message explaining that scores are only
    available after war completion.

    Args:
        interaction: The Discord interaction
        war_id: Optional war ID to show (defaults to most recent completed war)
        page: Page number to show (defaults to 1)
    """
    try:
        await interaction.followup.send("Generating leaderboard. Please wait...", ephemeral=True)

        # First, check if a specific war ID was requested
        target_war_id = war_id
        is_current_war = False

        # If no war ID specified, try to get most recent completed war from API
        if not target_war_id:
            await interaction.followup.send("No war ID specified. Checking for most recent completed war...", ephemeral=True)

        # Try to get official member scores for the war (only available for completed wars)
        sorted_contributors, is_official, war_data = await fetch_war_leaderboard_data(target_war_id)

        # If we don't have official scores, check if this is the current war
        if not sorted_contributors:
            current_war_id = current_war_data.get("war_id")
            is_current_war = target_war_id == current_war_id if target_war_id else False

            if is_current_war or (not target_war_id and current_war_id):
                # This is either the current war or no war ID was specified and we have an active war
                if not target_war_id:
                    target_war_id = current_war_id
                    is_current_war = True

                await interaction.followup.send(
                    "⚠️ This war is still in progress. "
                    "Official member scores are only available after war completion. "
                    "Try using the `/war result` command after the war ends.",
                    ephemeral=False
                )
                return
            else:
                # No data found for specified war ID
                await interaction.followup.send(
                    f"❌ Could not find completed war{' with ID ' + target_war_id if target_war_id else ''}. "
                    "If this war is still ongoing, please wait for it to finish.",
                    ephemeral=True
                )
                return

        # We now have official contributor data from the completed war

        # Extract war details from the data
        war_id = str(war_data.get("id"))
        start_time = datetime.fromtimestamp(war_data.get("start", 0)).strftime('%b %d, %Y')
        end_time = datetime.fromtimestamp(war_data.get("end", 0)).strftime('%b %d, %Y')

        # Find our faction
        our_faction = None
        opponent_faction = None

        for faction in war_data.get("factions", []):
            if int(faction.get("id", 0)) == FACTION_ID:
                our_faction = faction
            else:
                opponent_faction = faction

        if not our_faction or not opponent_faction:
            await interaction.followup.send("❌ Error processing war data: Could not identify factions.", ephemeral=True)
            return

        our_faction_name = our_faction.get("name", "Our Faction")
        opponent_name = opponent_faction.get("name", "Opponent")
        opponent_id = opponent_faction.get("id", 0)

        # Determine who won
        winner_id = war_data.get("winner", 0)
        we_won = int(winner_id) == FACTION_ID

        # Handle pagination
        items_per_page = 10
        total_items = len(sorted_contributors)
        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

        # Validate the requested page
        page = max(1, min(page, total_pages))

        # Slice the data for the current page
        start_index = (page - 1) * items_per_page
        end_index = min(start_index + items_per_page, total_items)
        page_data = sorted_contributors[start_index:end_index]

        # Create the embed with war information
        if we_won:
            title = f"🏆 WAR VICTORY: {our_faction_name} vs {opponent_name}"
            color = 0x1abc9c  # Green for victory
        else:
            title = f"⚔️ WAR DEFEAT: {our_faction_name} vs {opponent_name}"
            color = 0xe74c3c  # Red for defeat

        embed = discord.Embed(
            title=title,
            color=color,
            description=f"**Official Member Contributions**\nWar ID: {war_id} | {start_time} to {end_time}\nPage {page} of {total_pages}"
        )

        # Add member scores to the embed
        for i, member in enumerate(page_data, start=start_index + 1):
            member_name = member["name"]
            member_level = member["level"]
            member_attacks = member["attacks"]
            member_score = member["points"]

            embed.add_field(
                name=f"{i}. {member_name} [Lvl {member_level}]",
                value=f"**Score:** {member_score:,.2f}\n**Attacks:** {member_attacks}",
                inline=True
            )

            # Add a spacer after every 2 members for better readability
            if i % 2 == 0 and i < end_index:
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        # Add totals
        total_members = len(sorted_contributors)
        total_attacks = sum(member["attacks"] for member in sorted_contributors)
        total_score = sum(member["points"] for member in sorted_contributors)

        embed.add_field(
            name="📊 Summary",
            value=f"**Total Members:** {total_members}\n**Total Attacks:** {total_attacks}\n**Total Score:** {total_score:,.2f}",
            inline=False
        )

        # Set the footer
        embed.set_footer(text="Official Torn API data from completed war")

        # Create the pagination view
        class LeaderboardView(discord.ui.View):
            def __init__(self, current_page, total_pages, war_id):
                super().__init__(timeout=300)  # 5 minute timeout
                self.current_page = current_page
                self.total_pages = total_pages
                self.war_id = war_id
                self.message = None

                # Add link to Torn war page
                self.add_item(
                    discord.ui.Button(
                        label="View War Page",
                        url=f"https://www.torn.com/factions.php?step=profile&ID={FACTION_ID}#/tab=tab5",
                        style=discord.ButtonStyle.link
                    )
                )

                # Only add pagination buttons if there are multiple pages
                if total_pages > 1:
                    # First page button
                    first_page_button = discord.ui.Button(
                        label="<<",
                        custom_id="first_page",
                        style=discord.ButtonStyle.primary,
                        disabled=(current_page == 1)
                    )
                    first_page_button.callback = self.first_page_callback
                    self.add_item(first_page_button)

                    # Previous page button
                    prev_page_button = discord.ui.Button(
                        label="<",
                        custom_id="prev_page",
                        style=discord.ButtonStyle.primary,
                        disabled=(current_page == 1)
                    )
                    prev_page_button.callback = self.prev_page_callback
                    self.add_item(prev_page_button)

                    # Next page button
                    next_page_button = discord.ui.Button(
                        label=">",
                        custom_id="next_page",
                        style=discord.ButtonStyle.primary,
                        disabled=(current_page == total_pages)
                    )
                    next_page_button.callback = self.next_page_callback
                    self.add_item(next_page_button)

                    # Last page button
                    last_page_button = discord.ui.Button(
                        label=">>",
                        custom_id="last_page",
                        style=discord.ButtonStyle.primary,
                        disabled=(current_page == total_pages)
                    )
                    last_page_button.callback = self.last_page_callback
                    self.add_item(last_page_button)

            async def on_timeout(self):
                if self.message:
                    try:
                        await self.message.edit(view=None)
                    except:
                        pass

            async def first_page_callback(self, interaction):
                await interaction.response.defer()
                await show_leaderboard(interaction, self.war_id, 1)

            async def prev_page_callback(self, interaction):
                await interaction.response.defer()
                new_page = max(1, self.current_page - 1)
                await show_leaderboard(interaction, self.war_id, new_page)

            async def next_page_callback(self, interaction):
                await interaction.response.defer()
                new_page = min(self.total_pages, self.current_page + 1)
                await show_leaderboard(interaction, self.war_id, new_page)

            async def last_page_callback(self, interaction):
                await interaction.response.defer()
                await show_leaderboard(interaction, self.war_id, self.total_pages)

        # Create the view
        view = LeaderboardView(page, total_pages, war_id)

        # Send the embed with the view
        response = await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        view.message = response

        # Debug information
        print(f"Leaderboard displayed for war ID: {target_war_id}")
        print(f"Is current war: {is_current_war}")
    except Exception as e:
        print(f"Error displaying leaderboard: {str(e)}")
        await interaction.followup.send(f"❌ Error displaying leaderboard: {str(e)[:100]}...", ephemeral=True)

async def show_war_result(interaction: discord.Interaction, war_id: str = None):
    """Show detailed war result including rank changes and rewards"""
    try:
        await interaction.followup.send("Fetching war result data...", ephemeral=True)

        # Get the war data from the rankedwarreport endpoint
        _, _, war_data = await fetch_war_leaderboard_data(war_id)

        if not war_data:
            if war_id:
                await interaction.followup.send(f"❌ Could not find completed war with ID {war_id}. If this war is still ongoing, please wait for it to finish.", ephemeral=True)
            else:
                await interaction.followup.send("❌ Could not find any completed wars. Try again later or specify a specific war ID.", ephemeral=True)
            return

        # Process the war data into readable format
        start_time = datetime.fromtimestamp(war_data.get("start", 0)).strftime('%b %d, %Y %H:%M')
        end_time = datetime.fromtimestamp(war_data.get("end", 0)).strftime('%b %d, %Y %H:%M')
        war_id = war_data.get("id", "Unknown")
        winner_id = war_data.get("winner", 0)

        # Get faction data
        our_faction_data = None
        opponent_faction_data = None

        for faction in war_data.get("factions", []):
            if int(faction.get("id", 0)) == FACTION_ID:
                our_faction_data = faction
            else:
                opponent_faction_data = faction

        if not our_faction_data or not opponent_faction_data:
            await interaction.followup.send("❌ Error processing war data: Could not identify factions.", ephemeral=True)
            return

        # Get faction details
        our_faction_name = our_faction_data.get("name", "Our Faction")
        our_score = our_faction_data.get("score", 0)
        our_attacks = our_faction_data.get("attacks", 0)

        opponent_faction_name = opponent_faction_data.get("name", "Opponent Faction")
        opponent_id = opponent_faction_data.get("id", 0)
        opponent_score = opponent_faction_data.get("score", 0)
        opponent_attacks = opponent_faction_data.get("attacks", 0)

        # Determine who won
        we_won = int(winner_id) == FACTION_ID

        # Format the ranks if available
        rank_change = ""
        if "rank" in our_faction_data:
            before_rank = our_faction_data["rank"].get("before", "Unknown")
            after_rank = our_faction_data["rank"].get("after", "Unknown")

            if before_rank != after_rank:
                rank_change = f"\n**Rank Change:** {before_rank} → {after_rank}"
            else:
                rank_change = f"\n**Rank:** {after_rank} (unchanged)"

        # Format the rewards if available
        rewards_text = ""
        if "rewards" in our_faction_data:
            rewards = our_faction_data["rewards"]
            respect = rewards.get("respect", 0)
            points = rewards.get("points", 0)

            rewards_text = f"\n**Rewards:**\n• {respect:,} respect\n• {points:,} points"

            # Add items if any
            if "items" in rewards and rewards["items"]:
                rewards_text += "\n• Items:"
                for item in rewards["items"]:
                    item_name = item.get("name", "Unknown Item")
                    item_quantity = item.get("quantity", 1)
                    rewards_text += f"\n  - {item_quantity}x {item_name}"

        # Create the embed
        if we_won:
            title = f"💯 WAR VICTORY: {our_faction_name} vs {opponent_faction_name}"
            color = 0x1abc9c  # Green for victory
        else:
            title = f"⚔️ WAR DEFEAT: {our_faction_name} vs {opponent_faction_name}"
            color = 0xe74c3c  # Red for defeat

        embed = discord.Embed(
            title=title,
            color=color,
            description=f"**War ID:** {war_id}\n**Duration:** {start_time} to {end_time}{rank_change}{rewards_text}"
        )

        # Add score fields
        embed.add_field(
            name=f"{our_faction_name}",
            value=f"**Score:** {our_score:,}\n**Attacks:** {our_attacks:,}",
            inline=True
        )

        embed.add_field(
            name=f"{opponent_faction_name}",
            value=f"**Score:** {opponent_score:,}\n**Attacks:** {opponent_attacks:,}",
            inline=True
        )

        # Add helpful commands field
        embed.add_field(
            name="View More Details",
            value=f"Use `/war leaderboard {war_id}` to see member contributions",
            inline=False
        )

        # Set footer
        embed.set_footer(text=f"War ended {end_time}")

        # Send the embed
        response_msg = await interaction.followup.send(embed=embed, ephemeral=False)
        asyncio.create_task(scheduled_message_delete(response_msg))

    except Exception as e:
        error_msg = str(e)
        print(f"Error showing war result: {error_msg}")
        await interaction.followup.send(f"❌ Error showing war result: {error_msg[:100]}...", ephemeral=True)

async def show_my_stats(interaction: discord.Interaction, war_id: str = None):
    """View your contribution stats with data from API when possible"""
    try:
        await interaction.followup.send("Fetching your stats. Please wait...", ephemeral=True)
        member_id = interaction.user.id

        # If war_id is "all", show stats across all wars
        if war_id and war_id.lower() == "all":
            # Try to fetch API data for current war first
            current_war_id = current_war_data.get("war_id")
            api_attacks = []
            api_fetch_success = False

            if current_war_id and current_war_data.get("start_time", 0) > 0:
                await interaction.followup.send("Fetching real-time data from Torn API...", ephemeral=True)
                try:
                    # Try v2 API first
                    current_war_attacks = await fetch_attacks_from_api(current_war_data["start_time"])

                    if current_war_attacks:
                        # Filter to only this user's attacks
                        api_attacks = [
                            attack for attack in current_war_attacks
                            if str(attack["attacker_id"]) == str(member_id)
                        ]

                        if api_attacks:
                            api_fetch_success = True
                            await interaction.followup.send(f"Found {len(api_attacks)} of your attacks in API data!", ephemeral=True)
                        else:
                            await interaction.followup.send("No attacks found for you in API data.", ephemeral=True)

                except Exception as e:
                    # If API v2 fails, log it and try alternative sources
                    error_msg = str(e)
                    await interaction.followup.send(f"Error with API: {error_msg[:100]}...", ephemeral=True)
                    print(f"Error with API: {error_msg}")

            # Get attacks from all local logs
            manual_attacks = []
            for wid, war_log in attack_logs.items():
                attacks = [
                    attack for attack in war_log.get("attacks", [])
                    if str(attack["attacker_id"]) == str(member_id)
                ]
                manual_attacks.extend(attacks)

            # Combine both sources
            all_attacks = api_attacks + manual_attacks

            if not all_attacks:
                await interaction.followup.send("You haven't made any recorded attacks in any wars.", ephemeral=True)
                return

            total_attacks = len(all_attacks)
            total_points = sum(float(attack["points"]) for attack in all_attacks)
            avg_points = total_points / total_attacks if total_attacks else 0

            # Data source info
            data_source = []
            if api_attacks:
                data_source.append(f"{len(api_attacks)} from API")
            if manual_attacks:
                data_source.append(f"{len(manual_attacks)} from manual records")

            # Get last 5 attacks
            last_attacks = sorted(all_attacks, key=lambda a: a["timestamp"], reverse=True)[:5]
            last_attacks_text = ", ".join([f"{a['points']}pts" for a in last_attacks])

            # Create more detailed embed
            embed = discord.Embed(
                title="Your All-Time War Statistics",
                description=f"Stats for {interaction.user.display_name} across all wars:",
                color=0x1abc9c
            )

            # Main stats
            embed.add_field(name="Total Attacks", value=str(total_attacks), inline=True)
            embed.add_field(name="Total Points", value=f"{total_points:.1f}", inline=True)
            embed.add_field(name="Avg Points/Attack", value=f"{avg_points:.1f}", inline=True)

            # Attack details section
            if last_attacks:
                # Format last attacks with more detail
                attack_details = []
                for i, attack in enumerate(last_attacks):
                    # Get timestamp in readable format
                    timestamp = datetime.fromtimestamp(attack.get("timestamp", 0)).strftime('%Y-%m-%d %H:%M')

                    # Include result if available
                    result = f" ({attack.get('result', '')})" if 'result' in attack else ""

                    # Get target info if available
                    target_info = ""
                    defender_id = attack.get("defender_id", "")
                    if defender_id:
                        # Try to get defender name if stored
                        try:
                            user_data = await get_user_info(defender_id)
                            defender_name = user_data.get("name", "")
                            if defender_name:
                                target_info = f" → {defender_name}"
                        except:
                            pass

                    attack_details.append(f"**{i+1}.** {attack['points']} pts{result}{target_info} ({timestamp})")

                embed.add_field(
                    name="Recent Attacks", 
                    value="\n".join(attack_details) if attack_details else "None recorded", 
                    inline=False
                )

            # Add data source info in footer
            if data_source:
                footer_text = f"Data sources: {', '.join(data_source)}"
                embed.set_footer(text=footer_text)

            # Personal stats should stay visible longer (10 minutes)
            response_msg = await interaction.followup.send(embed=embed)
            asyncio.create_task(scheduled_message_delete(response_msg, 600))  # 10 minutes
            return

        # Otherwise, get stats for current or specific war
        current_war_id = current_war_data.get("war_id")
        target_war_id = war_id if war_id else current_war_id

        if not target_war_id:
            await interaction.followup.send("No active war and no war ID specified.")
            return

        # Get user stats
        stats = get_user_stats(member_id, target_war_id)

        if stats["total_attacks"] == 0:
            war_text = "current war" if target_war_id == current_war_id else f"war #{target_war_id}"
            await interaction.followup.send(f"You haven't made any recorded attacks in the {war_text}.")
            return

        # Find war details
        war_data = None
        if target_war_id == current_war_id:
            war_title = "Current War"
        else:
            war_title = f"War #{target_war_id}"
            # Look up in history
            for war in war_history:
                if str(war.get("war_id")) == str(target_war_id):
                    war_data = war
                    break

        # Get rankings if possible
        ranking = "Unknown"
        if target_war_id in attack_logs:
            war_attacks = attack_logs[target_war_id]["attacks"]

            # Group attacks by attacker
            attacker_stats = {}
            for attack in war_attacks:
                attacker_id = str(attack["attacker_id"])
                if attacker_id not in attacker_stats:
                    attacker_stats[attacker_id] = {
                        "total_points": 0,
                        "total_attacks": 0
                    }

                attacker_stats[attacker_id]["total_points"] += attack["points"]
                attacker_stats[attacker_id]["total_attacks"] += 1

            # Sort by total points
            ranked_attackers = sorted(
                attacker_stats.items(),
                key=lambda x: x[1]["total_points"],
                reverse=True
            )

            # Find your rank
            for i, (attacker_id, _) in enumerate(ranked_attackers):
                if str(attacker_id) == str(member_id):
                    ranking = f"#{i+1} of {len(ranked_attackers)}"
                    break

        # Create the embed
        embed = discord.Embed(
            title=f"Your {war_title} Statistics",
            description=f"Stats for {interaction.user.display_name}:",
            color=0x1abc9c
        )

        embed.add_field(name="Total Attacks", value=str(stats["total_attacks"]), inline=True)
        embed.add_field(name="Total Points", value=str(stats["total_points"]), inline=True)
        embed.add_field(name="Avg Points/Attack", value=f"{stats['average_points']:.1f}", inline=True)

        if ranking != "Unknown":
            embed.add_field(name="Ranking", value=ranking, inline=True)

        if stats["last_attacks"]:
            last_attacks_text = ", ".join([f"{pts}pts" for pts in stats["last_attacks"]])
            embed.add_field(name="Last Attacks", value=last_attacks_text, inline=False)

        embed.set_footer(text="Use /war record to log your attacks")

        response_msg = await interaction.followup.send(embed=embed)
        asyncio.create_task(scheduled_message_delete(response_msg, 300))  # 5 minutes
    except Exception as e:
        await interaction.followup.send(f"Error showing stats: {str(e)}")

async def record_attack_command(interaction: discord.Interaction, defender_id: int, points: float):
    """Record an attack for leaderboard tracking"""
    try:
        if not current_war_data.get("war_id"):
            await interaction.followup.send("❌ No active war to record attacks for.", ephemeral=True)
            return

        attacker_id = interaction.user.id
        success = record_attack(attacker_id, defender_id, points)

        if success:
            # Try to get defender info for better messaging
            try:
                user_data = await get_user_info(defender_id)
                name = user_data.get("name", f"User {defender_id}")
                defender_link = f"[{name}]({TORN_PROFILE_URL.format(defender_id)})"
                await interaction.followup.send(f"✅ Recorded attack against {defender_link} for {points} points.", ephemeral=True)
            except:
                await interaction.followup.send(f"✅ Recorded attack against {defender_id} for {points} points.", ephemeral=True)
        else:
            await interaction.followup.send("❌ Failed to record attack. No active war.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"Error recording attack: {str(e)}", ephemeral=True)

async def delete_attack_record(interaction: discord.Interaction, attack_id: int, war_id: str = None):
    """[ADMIN] Delete an attack record from the database"""
    try:
        # Determine which war to modify
        current_war_id = current_war_data.get("war_id")
        target_war_id = war_id if war_id else current_war_id

        if not target_war_id:
            await interaction.followup.send("❌ No active war and no war ID specified.", ephemeral=True)
            return

        # Check if we have data for this war
        if target_war_id not in attack_logs:
            await interaction.followup.send(f"❌ No attack data found for war #{target_war_id}.\n\nUse `/war logs` to see attack logs first.", ephemeral=True)
            return

        # Try to delete the specified attack
        deleted = False
        war_attacks = attack_logs[target_war_id]["attacks"]

        for i, attack in enumerate(war_attacks):
            if i + 1 == attack_id:  # Using 1-based indexing for user-friendliness
                deleted_attack = war_attacks.pop(i)

                # Get attacker and defender details if possible
                attacker_id = deleted_attack["attacker_id"] 
                defender_id = deleted_attack["defender_id"]
                points = deleted_attack["points"]

                try:
                    # Get attacker name
                    attacker = interaction.guild.get_member(int(attacker_id))
                    attacker_name = attacker.display_name if attacker else f"User {attacker_id}"

                    # Get defender name
                    try:
                        user_data = await get_user_info(defender_id)
                        defender_name = user_data.get("name", f"User {defender_id}")
                    except:
                        defender_name = f"User {defender_id}"

                    await interaction.followup.send(
                        f"✅ Deleted attack by {attacker_name} against {defender_name} "
                        f"({points} points) from war #{target_war_id}.",
                        ephemeral=True
                    )
        for war_id, war_data in wars.items():
            if "war" in war_data and war_data["war"].get("end", 1) == 0:
                active_war_info += f"War ID: {war_id}\n"
                for faction_id, faction_data in war_data.get("factions", {}).items():
                    active_war_info += f"Faction: {faction_data.get('name')} (ID: {faction_id})\n"
                    active_war_info += f"Score: {faction_data.get('score', 'N/A')}\n"

                active_war_info += f"Target: {war_data.get('war', {}).get('target', 'N/A')}\n"
                active_war_info += f"Start: {war_data.get('war', {}).get('start', 'N/A')}\n"
        active_war_info += "