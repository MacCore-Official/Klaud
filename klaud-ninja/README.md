# KLAUD-NINJA — AI Moderation & Command Bot

AI-powered Discord moderation and natural-language server management.
Multi-server, Supabase-backed, powered by Groq (llama-3.3-70b-versatile).

---

## Quick Start

### 1. Clone / copy the project

```
klaud-ninja/
├── cogs/           moderation, ai_commands, config
├── core/           bot, events
├── ai/             groq_client, interpreter, prompts
├── database/       supabase_client, queries, schema.sql
├── utils/          logger, permissions
├── main.py
├── requirements.txt
└── .env.example
```

### 2. Install dependencies

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Where to get it |
|---|---|
| `DISCORD_TOKEN` | [Discord Developer Portal](https://discord.com/developers/applications) → Bot → Token |
| `BOT_OWNER_ID` | Your Discord user ID (already pre-filled as `1269145029943758899`) |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) — free |
| `SUPABASE_URL` | Supabase project → Settings → API → Project URL |
| `SUPABASE_KEY` | Supabase project → Settings → API → **service_role** key |

### 4. Set up Supabase

1. Create a new Supabase project at [supabase.com](https://supabase.com).
2. Open the **SQL Editor** and paste the contents of `database/schema.sql`.
3. Click **Run**. Three tables will be created: `guild_settings`, `infractions`, `ai_logs`.

### 5. Enable Discord bot intents

In the [Developer Portal](https://discord.com/developers/applications):
- **Bot → Privileged Gateway Intents**
  - ✅ Server Members Intent
  - ✅ Message Content Intent

### 6. Run the bot

```bash
python main.py
```

---

## Commands

| Command | Description | Permission |
|---|---|---|
| `/klaud-intensity level:HIGH` | Set AI moderation intensity | Manage Server |
| `/klaud-ai toggle:on` | Enable/disable AI moderation | Manage Server |
| `/klaud-logchannel #channel` | Set the mod-log channel | Manage Server |
| `/klaud-config` | View current settings | Manage Server |
| `/klaud-infractions @user` | View a user's infraction history | Manage Server |
| `/klaud-ailogs` | View recent AI action log | Manage Server |
| `/klaud-test-ai question` | Free-form AI question | Manage Server |

### @Mention Commands (AI)

Mention the bot with any natural-language server management instruction:

```
@Klaud create a category called Trading with channels #buy-sell and #price-check
@Klaud lock this channel
@Klaud create 3 voice channels for gaming called Squad 1, Squad 2, AFK
@Klaud make a giveaways channel
@Klaud create roles for Admin, Moderator, and VIP
@Klaud rename #general to #chat
@Klaud purge 20 messages
```

---

## Moderation Intensity Levels

| Level | Threshold | Max Action |
|---|---|---|
| LOW | Severe violations only | Warn |
| MEDIUM | Obvious toxicity, harassment | Timeout |
| HIGH | Mild profanity, minor spam | Kick |
| EXTREME | Zero tolerance | Ban |

---

## Northflank / Docker Deployment

Build with the included `Dockerfile` or use Northflank's buildpack.

Set the same environment variables in Northflank's **Environment** section.
Build context: `klaud-ninja/`, start command: `python main.py`.

---

## Groq Models

Change `GROQ_MODEL` in `.env` to switch models:

| Model | Speed | Quality |
|---|---|---|
| `llama-3.3-70b-versatile` | Medium | ⭐⭐⭐ Best |
| `llama-3.1-8b-instant` | Fast | ⭐⭐ Good |
| `mixtral-8x7b-32768` | Medium | ⭐⭐⭐ Long context |
