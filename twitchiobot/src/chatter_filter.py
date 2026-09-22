"""
Automated-Account Filter

A survey records every account that sends a message during its window, and
chat bots send messages. A bot confined to one channel only inflates that
channel's author count. A bot shared by many channels is the real problem: one
account present in N channels adds a shared chatter to all N(N-1)/2 pairs among
them, which is exactly the signal the overlap graph is built from.

Measured over 2026-08-13..26 (47,864 channel rows, 898,094 accounts), automated
accounts supplied 97% of every channel-pair overlap increment in the data.
StreamElements and Nightbot alone were active in 2,903 and 2,801 channels.
GraphBuilder's max_viewer_channel_degree cap already skipped the few accounts
that large, but a long tail below it went straight into the edge counts:
service bots in 50-130 channels, and a farm of about a hundred generated
accounts chatting in step across casino streams.

Two rules remove them before any overlap is counted, and each catches what the
other cannot:

Known bot services (KNOWN_BOT_LOGINS)
    Many service bots answer commands rather than post on timers, so they are
    rarely in several chats at once and the concurrency rule never sees them.
    SoundAlerts and Sery_Bot were each in 100+ channels but never in more than
    three during the same window.

Concurrency (max_concurrent_channels)
    Every channel in a survey batch is observed during the same five-minute
    window, so an account's activity across that batch is simultaneous. A
    person can keep up with a couple of chats at a time. Measured by account
    age, accounts that peaked at one or two concurrent chats were
    indistinguishable from the population (11-12% issued in the farm's narrow
    band of recent account IDs, against 12% of everyone); at three it was 45%,
    at four 72%, at six or more 90%. More than three is where an account
    becomes likelier automated than not.

Both run during analysis rather than collection, so correcting either rule
applies retroactively to every retained survey. Only counts are reported:
which accounts were excluded is never logged or persisted, because a
behavioural rule can misjudge a person.
"""

from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Set, Tuple

#: More than this many channels inside one survey window marks an account as
#: automated. The shared default for AnalysisConfig and the threshold sweep.
DEFAULT_MAX_CONCURRENT_CHANNELS = 3

#: Accounts operated by chat-bot services that post in many broadcasters'
#: chats. Lowercase logins, because analysis still keys chatters by login.
#:
#: Channel-specific custom bots are deliberately absent. An account confined to
#: one channel creates no overlap, and guessing at them by name ("...bot")
#: catches people too: 955 of the 1,102 other bot-named accounts in the
#: measurement above appeared in a single channel. Add only verified services;
#: a deployment can extend the list with ``excluded_chatters``.
KNOWN_BOT_LOGINS = frozenset({
    # Moderation and command bots
    "botisimo",
    "botrixoficial",
    "fossabot",
    "moobot",
    "nightbot",
    "sery_bot",
    "streamelements",
    "streamlabs",
    "wizebot",
    # Alerts, media and integrations
    "blerp",
    "kofistreambot",
    "lumiastream",
    "pretzelrocks",
    "restreambot",
    "songlistbot",
    "soundalerts",
    "streamlootsbot",
    "streamstickers",
    "tangiabot",
    # Games and community bots
    "buttsbot",
    "pokemoncommunitygame",
    "potatbotat",
    "stay_hydrated_bot",
    "supibot",
    "titlechange_bot",
})


def _survey_window(snapshot: dict) -> Optional[Tuple[str, int]]:
    """The survey window a v2 row was observed in, or None for older shapes.

    Every channel in one EventSub batch shares one listening window, so
    (session, batch) names a set of chats watched at the same time. Legacy,
    CSV and VOD snapshots carry no such pairing and contribute no evidence.
    """
    session = snapshot.get("survey_session_id")
    if not isinstance(session, str) or not session:
        return None
    try:
        return session, int(snapshot.get("batch"))
    except (TypeError, ValueError):  # Absent, or NaN from a sparse column
        return None


def concurrency_peaks(snapshots: Iterable[dict]) -> Dict[str, int]:
    """Most channels each account was active in during a single survey window.

    Only accounts whose peak reaches two appear. They are the only ones a
    threshold can judge, and leaving out the single-channel majority keeps the
    result sized to a few thousand accounts rather than to every author in the
    analysis window.
    """
    windows: Dict[Tuple[str, int], List[dict]] = defaultdict(list)
    for snapshot in snapshots:
        key = _survey_window(snapshot)
        if key is not None:
            windows[key].append(snapshot)

    peaks: Dict[str, int] = {}
    for rows in windows.values():
        seen_channels: Set[str] = set()
        active: Counter = Counter()
        for row in rows:
            channel = str(row.get("channel") or row.get("channel_login") or "").lower()
            # A duplicated row is still one chat, not a second concurrent one.
            if channel in seen_channels:
                continue
            seen_channels.add(channel)
            active.update(set(row.get("chatters") or ()))
        for account, count in active.items():
            if count >= 2 and count > peaks.get(account, 0):
                peaks[account] = count
    return peaks


def remove_automated_chatters(
    channel_viewers: Dict[str, Set[str]],
    snapshots: Iterable[dict],
    *,
    exclude_known_bots: bool = True,
    excluded_chatters: Iterable[str] = (),
    max_concurrent_channels: Optional[int] = DEFAULT_MAX_CONCURRENT_CHANNELS,
) -> dict:
    """Remove automated accounts from every channel's viewer set, in place.

    ``snapshots`` must be the rows ``channel_viewers`` was built from, because
    the concurrency rule reads each row's survey window.

    Each excluded account is credited to the first rule that claims it —
    known bots, then ``excluded_chatters``, then concurrency — so the three
    counts sum to ``accounts`` and ``concurrent`` measures what the behavioural
    rule adds beyond the lists. The report carries counts only.
    """
    if max_concurrent_channels is not None and max_concurrent_channels < 1:
        raise ValueError("max_concurrent_channels must be at least 1 or None")

    known = KNOWN_BOT_LOGINS if exclude_known_bots else frozenset()
    listed = frozenset(
        login.strip().lower() for login in excluded_chatters if login.strip()
    ) - known
    concurrent: frozenset = frozenset()
    peaks: Dict[str, int] = {}
    if max_concurrent_channels is not None:
        peaks = concurrency_peaks(snapshots)
        concurrent = frozenset(
            account for account, peak in peaks.items()
            if peak > max_concurrent_channels
        ) - known - listed
    excluded = known | listed | concurrent

    degree: Counter = Counter()
    channels_affected = 0
    for viewers in channel_viewers.values():
        found = viewers & excluded
        if found:
            viewers -= found
            degree.update(found)
            channels_affected += 1

    return {
        "accounts": len(degree),
        "known_bots": sum(1 for account in degree if account in known),
        "listed": sum(1 for account in degree if account in listed),
        "concurrent": sum(1 for account in degree if account in concurrent),
        # (channel, account) memberships removed, and the channel-pair overlap
        # increments those accounts had contributed across the loaded channels.
        "memberships": sum(degree.values()),
        "channels": channels_affected,
        "pair_overlaps": sum(d * (d - 1) // 2 for d in degree.values()),
        # Accounts per peak above one, for re-calibrating the threshold: the
        # human curve decays steeply, and where it flattens is the cut.
        "peak_distribution": dict(sorted(Counter(peaks.values()).items())),
        "exclude_known_bots": exclude_known_bots,
        "max_concurrent_channels": max_concurrent_channels,
    }
