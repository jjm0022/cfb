Failed to create stream fd: Operation not permitted
Failed to create stream fd: Operation not permitted
Failed to create stream fd: Operation not permitted
# Discord Pick Reminder and Recommendation Monitor

## Purpose

Run a local Discord bot that reminds its single owner to submit weekly
pick'em selections and monitors market-driven recommendation changes. The bot
must reuse the existing Pickem application logic and must never turn routine
monitoring into a recorded submission history.

## Scope

- Send the owner a direct-message reminder at 10:00 AM America/New_York every
  Tuesday to submit the current week's picks.
- At 10:00 AM America/New_York every Wednesday through Monday, poll the live
  odds feed and calculate the active week's recommendations.
- Direct-message the owner only when the recommended side for one or more
  games differs from the last successful check.
- Provide Discord slash commands in the bot DM: `/status` and `/refresh`.
- Run locally as a durable `systemd` service.

The initial release does not submit picks to CBS, scrape CBS automatically,
support multiple owners, or send notices for mere line movement.

## Architecture

`pickem.discord_bot` is a thin integration layer. It owns the Discord Gateway
connection, application commands, Eastern-time scheduling, owner
authorization, and message formatting. It delegates odds polling and
recommendation generation to application-level services extracted from the
existing CLI wiring. Strategy remains in the established ingestion, edge, and
report modules.

The active sport, season, and week, plus the bot token and authorized Discord
user ID, are environment configuration. Secrets live in the existing ignored
`.env` and are never logged. The bot registers global application commands
that are enabled in the bot-DM context.

## Recommendation Monitoring

For each scheduled or manual refresh, the monitor:

1. validates the configured active week;
2. appends a fresh Odds API snapshot for that week;
3. derives the current ranked recommendations without invoking the CLI
   `report` command;
4. compares the canonical game-ID-to-recommended-side mapping with the most
   recent successful mapping; and
5. persists the new mapping and sends a DM only if the mapping changed.

The CLI's `report` command records every output as a pick batch intended for
later grading. Monitoring must use a non-recording service so repeated polling
does not fabricate submitted-pick history.

An automation-state table in the existing DuckDB database stores the active
week's last successful recommendation signature, the check timestamp, and the
last delivered failure fingerprint. It is distinct from `picks`.

## Commands and Notifications

`/status` returns the configured active week, next scheduled event, last
successful check, and the current known recommendations. `/refresh` runs the
same serialized monitor workflow immediately and reports whether the
recommendations changed; a change also produces the normal direct message.

Scheduled runs and `/refresh` share one process-local lock. This prevents
concurrent Odds API calls and duplicate messages.

Tuesday reminders are sent regardless of monitoring state. A scheduled refresh
failure logs diagnostics and sends one actionable DM for each distinct failure.
The same error is not repeated until a successful refresh or a changed error
fingerprint occurs.

## Operations

A repository-owned `systemd` unit and setup documentation will define how to
create and install the Discord application, supply `DISCORD_BOT_TOKEN` and
`DISCORD_OWNER_ID`, synchronize commands, enable the service, and inspect its
logs. The process restarts after a crash or host reboot.

## Verification

Unit and integration tests will use fake Discord, clock, scheduler, and
recommendation adapters. They cover schedule policy, authorization, unchanged
and changed recommendation sets, failure-notification deduplication, command
responses, and serialization. No test calls Discord or the Odds API.
