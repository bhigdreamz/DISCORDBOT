import discord
import requests
import asyncio
import datetime
import os

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
TORN_API_KEY = os.getenv('TORN_API_KEY')
YOUR_FACTION_ID = 42125
CHANNEL_ID = 1360732124033847387

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

client = discord.Client(intents=intents)
claimed_targets = {}


def get_opponent_faction():
    url = f"https://api.torn.com/faction/{YOUR_FACTION_ID}?selections=rankedwars&key={TORN_API_KEY}"
    res = requests.get(url).json()

    ranked_wars = res.get("ranked_wars", {})
    for war_id, info in ranked_wars.items():
        if info.get("status") == "war":
            return info["faction_opponent"]
    return None


def get_faction_members(faction_id):
    url = f"https://api.torn.com/faction/{faction_id}?selections=basic&key={TORN_API_KEY}"
    res = requests.get(url).json()
    return list(res.get("members", {}).keys())


def get_player_info(player_id):
    url = f"https://api.torn.com/user/{player_id}?selections=profile,personalstats,attacks,last,status&key={TORN_API_KEY}"
    return requests.get(url).json()


def is_attackable(player_data):
    status = player_data.get("status", {}).get("state", "")
    return status == "Okay"


def is_about_to_leave_hosp(player_data):
    status = player_data.get("status", {})
    if status.get("state") == "Hospital":
        hosp_time = status.get("hospital_timestamp", 0)
        remaining = hosp_time - int(datetime.datetime.now().timestamp())
        return 0 < remaining <= 60
    return False


def get_offline_time(player_data):
    last_action = player_data.get("last_action", {}).get("timestamp", 0)
    if last_action:
        delta = datetime.datetime.now() - datetime.datetime.fromtimestamp(
            last_action)
        return str(delta).split('.')[0]
    return "Unknown"


@client.event
async def on_ready():
    print(f'Logged in as {client.user}')
    channel = client.get_channel(CHANNEL_ID)

    opponent_faction_id = None
    opponent_members = []

    while True:
        # Check for ranked war opponent
        new_opponent_id = get_opponent_faction()
        if new_opponent_id and new_opponent_id != opponent_faction_id:
            opponent_faction_id = new_opponent_id
            opponent_members = get_faction_members(opponent_faction_id)
            await channel.send(
                f"**New Ranked War Detected!** Monitoring faction `{opponent_faction_id}`."
            )

        # If we have an opponent, monitor members
        if opponent_members:
            for member_id in opponent_members:
                try:
                    data = get_player_info(member_id)
                    name = data.get("name", "Unknown")

                    # Check attackable
                    if is_attackable(data):
                        msg = await channel.send(
                            f"**{name}** is **attackable**!\n"
                            f"Offline time: `{get_offline_time(data)}`\n"
                            f"React with ⚔️ to claim!")
                        await msg.add_reaction("⚔️")

                    # Check hospital timer
                    if is_about_to_leave_hosp(data):
                        await channel.send(
                            f"**{name}** will leave hospital in **1 minute**.")
                except Exception as e:
                    print(f"Error checking {member_id}: {e}")

        await asyncio.sleep(60)


@client.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    if reaction.emoji == "⚔️":
        message = reaction.message
        if message.id not in claimed_targets:
            claimed_targets[message.id] = user.name
            new_content = message.content + f"\n**Claimed by:** {user.mention}"
            await message.edit(content=new_content)


client.run(DISCORD_TOKEN)
