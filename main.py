import os
import asyncio
import discord
import aiohttp
from discord.ext import tasks, commands
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask
from threading import Thread

load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TORN_API_KEY = os.getenv("TORN_API_KEY")
FACTION_ID = int(os.getenv("FACTION_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

claimed_targets = {}
previous_war_id = None  # For tracking war end
bot.claimed_targets = claimed_targets  # Make it accessible to all commands

HEADERS = {"User-Agent": "AttackAlertBot/1.0"}

# KEEP-ALIVE FLASK SERVER
app = Flask('')


@app.route('/')
def home():
    return "I'm alive!"


def run():
    app.run(host='0.0.0.0', port=5000)


def keep_alive():
    Thread(target=run).start()


async def get_json(url):
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url) as resp:
            return await resp.json()


async def get_opponent_faction():
    url = f"https://api.torn.com/faction/{FACTION_ID}?selections=rankedwars&key={TORN_API_KEY}"
    data = await get_json(url)
    wars = data.get("rankedwars", {})
    for war_id, war_data in wars.items():
        if war_data["war"]["end"] == 0:
            factions = war_data["factions"]
            for fid in factions:
                if int(fid) != FACTION_ID:
                    return int(fid), war_id, war_data
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


@bot.command()
async def warstatus(ctx):
    try:
        opponent_id, war_id, war_data = await get_opponent_faction()
        if not war_data:
            await ctx.send("⚠️ No ongoing ranked war.")
            return

        lead = war_data["war"]["score"]["faction"] - war_data["war"]["score"]["opposing"]
        needed = abs(lead)
        estimated_attacks = (needed * 25) // 100 + 1  # assuming 25e per attack, 4 pts per attack

        await ctx.send(f"**Current War Status**\n"
                      f"Lead: `{lead} points`\n"
                      f"Approx. attacks to overtake: `{estimated_attacks}`")
    except Exception as e:
        await ctx.send("❌ Error checking war status. Try again later.")

@bot.command()
async def target(ctx, *, user_id: str = None):
    """Get detailed info about a specific target"""
    if not user_id:
        await ctx.send("❌ Please provide a target ID. Usage: `!target 1234567`")
        return
        
    try:
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
            description=f"**[{name} ({user_id})](https://www.torn.com/profiles.php?XID={user_id})**",
            color=0x1abc9c
        )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(name="Level", value=level, inline=True)
        embed.add_field(name="Last Active", value=last_active, inline=True)
        if faction_info:
            embed.add_field(name="Faction", value=faction_info.get("faction_name", "None"), inline=True)
            
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Attack",
                url=f"https://www.torn.com/loader.php?sid=attack&user2ID={user_id}",
                style=discord.ButtonStyle.link
            )
        )
        
        await ctx.send(embed=embed, view=view)
    except Exception as e:
        await ctx.send(f"Error getting target info: {str(e)}")

@bot.command()
async def unclaim(ctx, user_id: int):
    """Remove a claim on a target"""
    if user_id in claimed_targets:
        del claimed_targets[user_id]
        await ctx.send(f"Unclaimed target {user_id}")
    else:
        await ctx.send("This target was not claimed.")

@bot.command()
async def claims(ctx):
    """Show all currently claimed targets"""
    if not claimed_targets:
        await ctx.send("No targets are currently claimed.")
        return
        
    embed = discord.Embed(title="Currently Claimed Targets", color=0x1abc9c)
    for target_id, claimer_id in claimed_targets.items():
        claimer = ctx.guild.get_member(claimer_id)
        claimer_name = claimer.display_name if claimer else "Unknown"
        embed.add_field(
            name=f"Target ID: {target_id}",
            value=f"Claimed by: {claimer_name}",
            inline=False
        )
    await ctx.send(embed=embed)

@bot.command()
async def commands(ctx):
    """Show all available commands"""
    cmd_list = {
        "!warstatus": "Shows current war status and points needed",
        "!target <user_id>": "Get detailed info about a specific target",
        "!unclaim <user_id>": "Remove a claim on a target",
        "!claims": "Show all currently claimed targets",
        "!commands": "Show this help message"
    }
    
    embed = discord.Embed(
        title="Available Commands",
        description="Here are all the available commands:",
        color=0x1abc9c
    )
    
    for cmd, desc in cmd_list.items():
        embed.add_field(name=cmd, value=desc, inline=False)
        
    await ctx.send(embed=embed)


@tasks.loop(seconds=30)
async def check_targets():
    global previous_war_id
    channel = bot.get_channel(CHANNEL_ID)
    opponent_id, war_id, war_data = await get_opponent_faction()

    # Check for war end
    if previous_war_id and war_id != previous_war_id:
        await announce_war_result(previous_war_id, channel)
        previous_war_id = None

    if not opponent_id:
        return

    previous_war_id = war_id
    members = await get_opponent_members(opponent_id)
    for member_id, member in members.items():
        status = member.get("status", {})
        name = member.get("name", "Unknown")

        if await is_attackable(status):
            if member_id in claimed_targets:
                continue

            user_data = await get_user_info(member_id)
            level = user_data.get("level", "N/A")
            last_active = user_data.get("last_action",
                                        {}).get("relative", "Unknown")
            days_faction = user_data.get("faction",
                                         {}).get("days_in_faction", "N/A")

            profile_link = f"https://www.torn.com/profiles.php?XID={member_id}"
            embed = discord.Embed(
                title=f"Target Available",
                description=f"**[{name} ({member_id})]({profile_link})**",
                color=0x1abc9c)
            embed.add_field(name="Status", value=status["state"], inline=True)
            embed.add_field(name="Level", value=level, inline=True)
            embed.add_field(name="Last Active", value=last_active, inline=True)
            embed.add_field(name="Days in Faction",
                            value=days_faction,
                            inline=True)

            if status["state"] == "Hospital":
                seconds_left = status.get("until", 0) - int(
                    datetime.now().timestamp())
                embed.add_field(name="Leaving Hospital",
                                value=f"{seconds_left} sec",
                                inline=True)

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="Attack",
                    url=
                    f"https://www.torn.com/loader.php?sid=attack&user2ID={member_id}",
                    style=discord.ButtonStyle.link))

            msg = await channel.send(embed=embed, view=view)
            await msg.add_reaction("⚔️")

            def check(reaction, user):
                return reaction.message.id == msg.id and str(
                    reaction.emoji) == "⚔️" and not user.bot

            try:
                reaction, user = await bot.wait_for("reaction_add",
                                                    timeout=60.0,
                                                    check=check)
                embed.set_footer(text=f"Claimed by {user.display_name}")
                await msg.edit(embed=embed)
                claimed_targets[member_id] = user.id
            except asyncio.TimeoutError:
                pass


async def announce_war_result(war_id, channel):
    url = f"https://api.torn.com/faction/{FACTION_ID}?selections=rankedwars&key={TORN_API_KEY}"
    data = await get_json(url)
    war_data = data.get("rankedwars", {}).get(str(war_id), {})
    if not war_data:
        return

    factions = war_data.get("factions", {})
    rewards = war_data.get("rewards", {})
    start = datetime.fromtimestamp(
        war_data["war"]["start"]).strftime('%H:%M:%S - %d/%m/%y')
    end = datetime.fromtimestamp(
        war_data["war"]["end"]).strftime('%H:%M:%S - %d/%m/%y')

    lines = [
        f"**Ranked War Report**", f"**Ranked War #{war_id}**",
        f"{start} until {end}"
    ]

    for fid, info in factions.items():
        name = info.get("name", "Unknown")
        result = "won" if info.get("result") == "win" else "lost"
        rank = info.get("rank", "Unknown")
        reward = rewards.get(fid, {})
        respect = reward.get("bonus_respect", 0)
        points = reward.get("points", 0)
        caches = ", ".join(reward.get("rank_rewards", [])) or "No cache"
        lines.append(
            f"{name} {result.upper()} and received {respect} bonus respect, {points} points, {caches}"
        )

    await channel.send("\n".join(lines))


@bot.command()
async def company(ctx, *, user_id: str = None):
    """Get company info about a player"""
    if not user_id:
        await ctx.send("❌ Please provide a player ID. Usage: `!company 1234567`")
        return
        
    try:
        user_id = int(''.join(filter(str.isdigit, user_id)))
        user_data = await get_json(f"https://api.torn.com/user/{user_id}?selections=profile&key={TORN_API_KEY}")
        
        job = user_data.get("job", {})
        company_name = job.get("company_name", "Unemployed")
        company_position = job.get("position", "N/A")
        
        embed = discord.Embed(
            title="Company Information",
            description=f"**[{user_data.get('name')}](https://www.torn.com/profiles.php?XID={user_id})**",
            color=0x1abc9c
        )
        embed.add_field(name="Company", value=company_name, inline=True)
        embed.add_field(name="Position", value=company_position, inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error getting company info: {str(e)}")

@bot.command()
async def faction(ctx, *, input_id: str = None):
    """Get info about a faction or player's faction"""
    if not input_id:
        await ctx.send("❌ Please provide an ID. Usage: `!faction <user_id/faction_id>`")
        return
        
    try:
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
        leader = faction_data.get("leader", "Unknown")
        
        embed = discord.Embed(
            title="Faction Information",
            description=f"**[{name}](https://www.torn.com/factions.php?step=profile&ID={faction_id})**",
            color=0x1abc9c
        )
        embed.add_field(name="Leader", value=leader, inline=True)
        embed.add_field(name="Members", value=members_count, inline=True)
        embed.add_field(name="Respect", value=respect, inline=True)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error getting faction info: {str(e)}")

@bot.command()
async def claim(ctx, user_id: int):
    """Claim a target"""
    claimed_targets[user_id] = ctx.author.id
    await ctx.send(f"Target {user_id} claimed by {ctx.author.display_name}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    check_targets.start()


keep_alive()
bot.run(DISCORD_TOKEN)
