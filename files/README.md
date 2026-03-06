# Klaud Bot 🤖

**AI-powered Discord server manager with license gating, Groq AI, and Supabase.**

---

## Features

| Feature | Description |
|---|---|
| 🔐 License System | Per-server license activation, validation, and transfers |
| 🤖 AI Moderation | Groq-powered automod: detects swearing, harassment, spam, scams, toxicity |
| ⚠️ Warning System | Escalating punishments: warn → timeout → ban, all stored in Supabase |
| 🏗️ Server Builder | One-sentence server creation: channels, roles, rules, welcome messages |
| 📋 Custom AI Rules | Admins define rules in plain English; AI enforces them automatically |
| 💾 Templates | Save and restore full server layouts |

---

## Quick Start

### 1. Clone and install

```bash
git clone <your-repo-url> klaud-bot
cd klaud-bot
pip install -r requirements.txt
```

### 2. Create a `.env` file

```env
# Discord
DISCORD_TOKEN=your_discord_bot_token_here

# Groq AI
GROQ_API_KEY=your_groq_api_key_here
GROQ_MODEL=llama3-70b-8192

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_service_role_key_here

# Optional overrides
BOT_PREFIX=!
LOG_LEVEL=INFO
LICENSE_CHECK_INTERVAL=3600
DEFAULT_WARN_LIMIT=3
DEFAULT_TIMEOUT_MINUTES=10
```

### 3. Set up Supabase tables

Run this SQL in your Supabase **SQL Editor**:

```sql
-- Licenses
create table licenses (
  id uuid primary key default gen_random_uuid(),
  guild_id text unique not null,
  license_key text not null,
  owner_id text not null,
  activated_at timestamptz default now(),
  expires_at timestamptz,
  active boolean default true
);

-- Warnings
create table warnings (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  user_id text not null,
  moderator_id text not null,
  reason text not null,
  created_at timestamptz default now()
);

-- Moderation Logs
create table mod_logs (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  action text not null,
  target_id text not null,
  actor_id text not null,
  detail text,
  created_at timestamptz default now()
);

-- Custom AI Prompts
create table custom_prompts (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  prompt_name text not null,
  prompt_text text not null,
  created_by text not null,
  updated_at timestamptz default now(),
  unique (guild_id, prompt_name)
);

-- Server Templates
create table server_templates (
  id uuid primary key default gen_random_uuid(),
  guild_id text not null,
  template_name text not null,
  template_json text not null,
  created_at timestamptz default now(),
  unique (guild_id, template_name)
);
```

### 4. Create your Discord bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a new application → Bot
3. Enable **Message Content Intent**, **Server Members Intent**, **Presence Intent**
4. Copy the token into `.env`
5. Invite with this permission integer: `8` (Administrator) or scope it down as needed

### 5. Run the bot

```bash
python bot.py
```

---

## Slash Commands

### License
| Command | Permission | Description |
|---|---|---|
| `/activate-license key` | Administrator | Activate a license for the server |
| `/license-info` | Administrator | View current license status |
| `/transfer-license user` | License owner | Transfer license to another user |

### AI Moderation Rules
| Command | Permission | Description |
|---|---|---|
| `/ai-prompt add name text` | Administrator | Add a custom AI moderation rule |
| `/ai-prompt list` | Administrator | List all active custom rules |
| `/ai-prompt remove name` | Administrator | Remove a custom rule |
| `/ai-prompt test message` | Administrator | Test AI moderation with a sample message |

### Warnings
| Command | Permission | Description |
|---|---|---|
| `/warnings view user` | Moderate Members | View warnings for a user |
| `/warnings add user reason` | Moderate Members | Manually warn a user |
| `/warnings clear user` | Moderate Members | Clear all warnings for a user |

### Server Builder
| Command | Permission | Description |
|---|---|---|
| `/build server instruction` | Administrator | Build server from plain-English description |
| `/build template save name` | Administrator | Save current layout as template |
| `/build template load name` | Administrator | Restore a saved template |

---

## Custom AI Rules — Examples

```
/ai-prompt add name:swearing_rule text:If someone swears, warn them. On the 3rd warning timeout for 10 minutes.
/ai-prompt add name:no_links text:Delete any messages containing links to external websites unless the user has the Trusted role.
/ai-prompt add name:spam_rule text:If someone sends the same message more than 3 times in a row, delete and timeout for 5 minutes.
```

---

## Folder Structure

```
klaud-bot/
├─ bot.py                  # Main runner
├─ config.py               # Environment config
├─ requirements.txt
├─ README.md
├─ ai/
│   ├─ core.py             # Groq AI wrapper (moderation, server planner, admin Q&A)
│   └─ prompt_manager.py   # Per-guild custom prompt cache + Supabase sync
├─ commands/
│   ├─ license.py          # /activate-license, /license-info, /transfer-license
│   ├─ ai_prompt.py        # /ai-prompt group
│   └─ server_builder.py   # /build server, /build template
├─ moderation/
│   ├─ automod.py          # on_message listener → AI check → actions
│   └─ warnings.py         # /warnings group + issue_warning() helper
└─ database/
    └─ db_client.py        # All Supabase queries
```

---

## Architecture Notes

- **All AI calls** go through `ai/core.py` which wraps the Groq `AsyncGroq` client.
- **All DB operations** are in `database/db_client.py` — swap Supabase for another backend here.
- **License checks** happen on startup, periodically (every `LICENSE_CHECK_INTERVAL` seconds), and before every licensed command.
- **Automod** runs on every non-bot message and skips admins/server owners.
- **Escalation**: warn → timeout (multiplied by repeat count) → ban at 2× warn limit.
- Dangerous operations (`/build server`, `/build template load`) require button confirmation.

---

## License

MIT — use freely, but keep Klaud awesome. 🚀
