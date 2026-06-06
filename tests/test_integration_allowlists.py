from app.integrations.discord import _discord_sender_allowed, _parse_id_list as parse_discord_ids
from app.integrations.telegram import _parse_id_list as parse_telegram_ids, _telegram_sender_allowed


def test_telegram_allowlist_accepts_chat_or_user_id():
    assert parse_telegram_ids("123, 456;bad") == {123, 456}
    assert _telegram_sender_allowed(123, None, allowed_chat_ids={123}, allowed_user_ids=set())
    assert _telegram_sender_allowed(None, 456, allowed_chat_ids=set(), allowed_user_ids={456})
    assert not _telegram_sender_allowed(999, 888, allowed_chat_ids={123}, allowed_user_ids={456})


def test_discord_allowlist_accepts_user_channel_or_guild_id():
    assert parse_discord_ids("11, 22;bad") == {11, 22}
    assert _discord_sender_allowed(1, 2, None, allowed_user_ids={1}, allowed_channel_ids=set(), allowed_guild_ids=set())
    assert _discord_sender_allowed(1, 2, None, allowed_user_ids=set(), allowed_channel_ids={2}, allowed_guild_ids=set())
    assert _discord_sender_allowed(1, 2, 3, allowed_user_ids=set(), allowed_channel_ids=set(), allowed_guild_ids={3})
    assert not _discord_sender_allowed(9, 8, 7, allowed_user_ids={1}, allowed_channel_ids={2}, allowed_guild_ids={3})
