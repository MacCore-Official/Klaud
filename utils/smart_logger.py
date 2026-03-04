import discord
from database.connection import db

async def log_action(guild: discord.Guild, title: str, user: discord.Member, reason: str, color: discord.Color):
    if not db.pool: return
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow("SELECT log_channel_id FROM guild_settings WHERE guild_id = $1", guild.id)
        if row and row['log_channel_id'] != 0:
            channel = guild.get_channel(row['log_channel_id'])
            if channel:
                embed = discord.Embed(title=title, color=color)
                if user: embed.add_field(name="User", value=f"{user.mention} ({user.id})")
                embed.add_field(name="Details", value=reason, inline=False)
                await channel.send(embed=embed)
