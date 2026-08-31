# Discord pick reminder operations

This runbook installs the owner-only Discord bot as a user `systemd` service.
The bot sends the configured owner a Tuesday reminder and monitors the active
week on the other days. It does not submit picks to CBS. Commands and refreshes
use the project database and the API keys already required by Pickem.

The service unit assumes the canonical checkout is
`/home/jmiller/cfb`; if the checkout moves, update both the unit and the
commands in this runbook together.

## 1. Create and configure the Discord application

Complete these steps in the [Discord Developer Portal](https://discord.com/developers/applications)
while signed in as the Discord account that will own the bot:

1. Select **New Application**, enter a name, and select **Create**.
2. Open **Bot**, select **Add Bot**, and confirm. Under the bot's token
   controls, select **Reset Token**, then **Copy** the new token. Treat it as
   a password; it is shown only when Discord issues it.
3. Open **Installation**. Under **Installation Contexts**, enable **User
   Install**. Set **Install Link** to **Discord Provided Link**, then under
   **Default Install Settings → User Install** add the
   `applications.commands` scope. The `bot` scope is for a guild installation,
   not a user installation; this private bot needs no guild install,
   privileged intent, or guild permission.
4. Keep the **Bot DM** (`BOT_DM`) interaction context enabled for the
   application commands. The Pickem bot registers `/status` and `/refresh`
   with that context at startup, so use the bot's direct-message conversation
   (not a guild channel or an unrelated group DM). Discord documents this as
   the command's interaction context; it is distinct from the **User Install**
   installation context.
5. Copy the generated install link, open it in the owner account, choose the
   **User Install** / **Add to my apps** option (not a server install), and
   select **Authorize**. This installs the application to the owner account.

To obtain the owner ID, enable **Developer Mode** in Discord under **User
Settings → Advanced**, then right-click the owner account and select **Copy
User ID**. The ID is a numeric value and is not the bot token.

## 2. Create the environment file

Create `/home/jmiller/cfb/.env` on the host where the service will run. Do not
commit it or paste its contents into a ticket or log. Only credentials are
required:

```dotenv
DISCORD_BOT_TOKEN=paste-the-token-here
DISCORD_OWNER_ID=123456789012345678
```

The bot's refresh path needs the existing Odds API credential. Add it if it is
not already present in the file:

```dotenv
ODDS_API_KEY=paste-the-odds-api-key-here
```

The CFBD credential is used by separate CFBD ingest/result commands, not by
this long-running Discord refresh process, so it is not a service requirement.

Keep the file readable only by the account running the user service:

```bash
chmod 600 /home/jmiller/cfb/.env
```

Set the database path, timezone, and schedules in the tracked
`/home/jmiller/cfb/config/discord-bot.yaml`. The bot automatically discovers
each sport's current stored pick week, keeping it active until all of its pick
games have final scores. No weekly configuration edit or restart is required.

`/status` and `/refresh` also accept optional `season` and `week` arguments.
Supply both to view or refresh every sport with stored picks for that exact
week; omit both for the automatically selected current scopes.

The database defaults to `data/pickem.duckdb` under the project directory.
Run the normal data-ingest and preflight workflow before asking the bot to
refresh a week.

## 3. Start interactively once

Run the bot in the foreground first. This verifies that the environment file,
database path, credentials, and Discord installation are usable before a
background service is enabled:

```bash
cd /home/jmiller/cfb
uv run pickem-discord-bot
```

Leave it running long enough to see a successful Discord login and command
synchronization. In the owner's Discord DMs, confirm that `/status` and
`/refresh` are available. Stop the foreground process with `Ctrl-C` before
installing the service. A global command can take time to appear in Discord;
do not create a second bot or repeatedly reset the token while waiting.

## 4. Install the user service

User services stop when the user logs out unless lingering is enabled. Enable
linger for the account that owns the checkout and `.env`:

```bash
loginctl enable-linger $USER
```

Copy the repository unit into the per-user `systemd` unit directory, reload
the user manager, and enable the bot at login and boot:

```bash
mkdir -p ~/.config/systemd/user
cp /home/jmiller/cfb/deploy/pickem-discord-bot.service \
  ~/.config/systemd/user/pickem-discord-bot.service
systemctl --user daemon-reload
systemctl --user show pickem-discord-bot.service \
  --property=Environment --property=ExecStart --property=WorkingDirectory
systemctl --user enable --now pickem-discord-bot
```

The unit uses `WorkingDirectory=/home/jmiller/cfb` and loads
`EnvironmentFile=/home/jmiller/cfb/.env`, so both paths must be accessible to
the same user. The `systemctl --user show` output must include
`PATH=/home/jmiller/.local/bin:/usr/local/bin:/usr/bin`; this checks the
service manager's loaded environment rather than only the interactive shell's
`PATH`. `systemd --user` does not read a shell's exported variables as a
replacement for this environment file.

## 5. Check status and logs

Use these commands after installation and whenever an alert or scheduled run
needs investigation:

```bash
systemctl --user status pickem-discord-bot --no-pager
journalctl --user -u pickem-discord-bot --since today --no-pager
journalctl --user -u pickem-discord-bot -f
```

`status` should show `active (running)`. The service restarts after a process
failure with a five-second delay. A failed start commonly means a missing
`.env`, an invalid numeric `DISCORD_OWNER_ID`/week value, or a token rejected
by Discord. Check the unit status and sanitized application error messages;
never print the environment file or include `DISCORD_BOT_TOKEN` in a support
command.

For a configuration change, edit `.env`, then reload and restart the user
service:

```bash
systemctl --user daemon-reload
systemctl --user restart pickem-discord-bot
systemctl --user status pickem-discord-bot --no-pager
```

`daemon-reload` is harmless after an environment-only change, while the
restart is required for the process to read the new values.

## 6. Rotate a token safely

Rotate a token immediately if it was exposed. In the Developer Portal open
the application, go to **Bot**, choose **Reset Token**, and copy the new token.
Resetting invalidates the old token, so the running process will eventually
disconnect until restarted.

On the host, replace only the `DISCORD_BOT_TOKEN` line in
`/home/jmiller/cfb/.env`, preserve mode `600`, and restart the service:

```bash
chmod 600 /home/jmiller/cfb/.env
systemctl --user restart pickem-discord-bot
systemctl --user status pickem-discord-bot --no-pager
journalctl --user -u pickem-discord-bot -n 50 --no-pager
```

Confirm that the new process logs in successfully, then revoke or delete any
old token copies. Never put a token in the unit file, shell history, source
control, or a pasted journal excerpt.
