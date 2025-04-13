import os
import asyncio
import discord
import aiohttp
from discord.ext import tasks
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TORN_API_KEY = os.getenv("TORN_API_KEY")
FACTION_ID = int(os.getenv("FACTION_ID"))
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
HEADERS = {"User-Agent": "AttackAlertBot/1.0"}

intents = discord.Intents.default()
intents.messages = True
intents.reactions = True
client = discord.Client(intents=intents)

claimed_targets = {}

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    check_targets.start()

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
                    return int(fid)
    return None

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

@tasks.loop(seconds=30)
async def check_targets():
    channel = client.get_channel(CHANNEL_ID)
    opponent_faction = await get_opponent_faction()
    if not opponent_faction:
        print("No opponent faction found.")
        return

    members = await get_opponent_members(opponent_faction)
    for member_id, member in members.items():
        status = member.get("status", {})
        name = member.get("name", "Unknown")
        if await is_attackable(status):
            if member_id in claimed_targets:
                continue  # Already claimed

            user_data = await get_user_info(member_id)
            level = user_data.get("level", "N/A")
            last_active = user_data.get("last_action", {}).get("relative", "Unknown")
            days_in_faction = user_data.get("faction", {}).get("days_in_faction", "N/A")

            profile_link = f"https://www.torn.com/profiles.php?XID={member_id}"
            embed = discord.Embed(title=f"Target Available",
                                  description=f"**[{name} ({member_id})]({profile_link})**",
                                  color=0x1abc9c)
            embed.add_field(name="Status", value=status["state"], inline=True)
            embed.add_field(name="Level", value=level, inline=True)
            embed.add_field(name="Last Active", value=last_active, inline=True)
            embed.add_field(name="Days in Faction", value=str(days_in_faction), inline=True)

            if status['state'] == 'Hospital':
                seconds_left = status.get("until", 0) - int(datetime.now().timestamp())
                embed.add_field(name="Leaving Hospital", value=f"{seconds_left} sec", inline=True)

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Attack", url=f"https://www.torn.com/loader.php?sid=attack&user2ID={member_id}", style=discord.ButtonStyle.link))

            msg = await channel.send(embed=embed, view=view)
            await msg.add_reaction("⚔️")

            def check(reaction, user):
                return reaction.message.id == msg.id and str(reaction.emoji) == "⚔️" and not user.bot

            try:
                reaction, user = await client.wait_for("reaction_add", timeout=60.0, check=check)
                embed.set_footer(text=f"Claimed by {user.display_name}")
                await msg.edit(embed=embed)
                claimed_targets[member_id] = user.id
            except asyncio.TimeoutError:
                pass

client.run(DISCORD_TOKEN)