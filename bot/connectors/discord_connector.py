"""Discord connector.

The Discord bot uses the discord.py cog pattern (ChatCog in bot/commands/chat.py)
rather than subclassing BasePlatform directly. This is because discord.py manages
the bot lifecycle (connect, reconnect, disconnect) internally via the discord.Client
class that GSABot inherits from.

ChatCog delegates all business logic to MessageHandler, achieving the same
platform-agnostic separation as TelegramConnector without an additional wrapper layer:

    Discord:  GSABot (discord.Client) + ChatCog (commands.Cog) -> MessageHandler
    Telegram: TelegramConnector (BasePlatform) -> MessageHandler
    Future:   XConnector (BasePlatform) -> MessageHandler

See bot/commands/chat.py for the Discord message handling implementation.
See bot/main.py for the Discord bot entry point.
"""
