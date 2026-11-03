"""Controls whatever is playing through Windows' own media controls: no focus change, keystrokes or network."""
import asyncio

from .winsys import on_com_thread

METHODS = {"Play": "try_play_async", "Pause": "try_pause_async", "Next": "try_skip_next_async", "Previous": "try_skip_previous_async"}


async def _sessions() -> list:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    return list((await Manager.request_async()).get_sessions())


def _playing(session) -> bool:
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
    return session.get_playback_info().playback_status == Status.PLAYING


async def _session_of(player: str):
    """The one that's playing, as one browser can have several of these under its one name."""
    found = [session for session in await _sessions() if session.source_app_user_model_id == player]
    return next((session for session in found if _playing(session)), found[0] if found else None)


def _do(work):
    return on_com_thread(lambda: asyncio.run(work()))


def players() -> list[str]:
    async def work():
        return sorted({session.source_app_user_model_id for session in await _sessions()})
    return _do(work)


def is_playing(player: str) -> bool:
    async def work():
        session = await _session_of(player)
        return bool(session) and _playing(session)
    return _do(work)


def active() -> str | None:
    """The player that's playing now, or else the first one that exists."""
    found = players()
    return next((player for player in found if is_playing(player)), found[0] if found else None)


def title(player: str) -> str:
    async def work():
        session = await _session_of(player)
        details = await session.try_get_media_properties_async() if session else None
        return details.title if details else ""
    return _do(work)


def command(player: str, method: str) -> None:
    async def work():
        session = await _session_of(player)
        if session:
            await getattr(session, METHODS[method])()
    _do(work)


def pause_all() -> None:
    async def work():
        for session in await _sessions():
            if _playing(session):
                await session.try_pause_async()
    _do(work)
