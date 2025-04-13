import os
import asyncio
import discord
import aiohttp
from discord.ext import tasks
from dotenv import load_dotenv
from datetime import datetime
from flask import Flask

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

# Initialize Flask app for keep-alive
app = Flask(__name__)


@app.route('/')
def home():
    return "Bot is running"


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    check_targets.start()


async def get_json(url):
    async with aiohttp.ClientSession(headers=HEADERS) as session:
        async with session.get(url) as resp:
            return await resp.json()


async def get_ranked_war_info():
    url = f"https://api.torn.com/faction/{FACTION_ID}?selections=rankedwars&key={TORN_API_KEY}"
    data = await get_json(url)
    wars = data.get("rankedwars", {})

    for war_id, war_data in wars.items():
        if war_data["war"]["end"] == 0:
            return war_data  # Active ranked war found
    return None  # No active ranked war


async def get_faction_rewards(war_data):
    # Extracting rewards from the war data
    faction_rewards = {}
    for faction_id, faction_data in war_data["factions"].items():
        faction_name = faction_data["name"]
        faction_score = faction_data["score"]
        faction_chain = faction_data["chain"]
        if war_data["war"].get("winner") == int(faction_id):
            # If this faction won, display their rewards
            rewards = war_data["war"].get("rewards", {}).get(faction_id, {})
            faction_rewards[faction_name] = {
                "score": faction_score,
                "chain": faction_chain,
                "rewards": rewards
            }
    return faction_rewards


async def post_ranked_war_result(war_data):
    channel = client.get_channel(CHANNEL_ID)

    # Get the reward details for each faction
    faction_rewards = await get_faction_rewards(war_data)

    # Prepare the announcement message
    embed = discord.Embed(
        title=f"Ranked War Report",
        description=f"**Ranked War #{war_data['war']['target']}**\n"
        f"{war_data['factions'][str(war_data['war']['winner'])]['name']} has defeated "
        f"{war_data['factions'][str(FACTION_ID)]['name']} in a ranked war",
        color=0x1abc9c)

    for faction_name, rewards in faction_rewards.items():
        embed.add_field(
            name=
            f"{faction_name} - Score: {rewards['score']} (Chain: {rewards['chain']})",
            value=f"**Rewards**: {rewards['rewards']}",
            inline=False)

    await channel.send(embed=embed)


@tasks.loop(minutes=10)  # Check every 10 minutes
async def check_targets():
    war_data = await get_ranked_war_info()

    if war_data:
        await post_ranked_war_result(war_data)
    else:
        print("No active ranked war found.")


# Keep-alive feature
def run_flask():
    app.run(host='0.0.0.0', port=5000)


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.create_task(client.start(DISCORD_TOKEN))  # Start the Discord bot
    loop.run_in_executor(
        None, run_flask)  # Run Flask in the background to keep the bot alive
