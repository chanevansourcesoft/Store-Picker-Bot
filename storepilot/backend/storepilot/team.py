"""
The team password (STOREPILOT_UPLOAD_PASSWORD) guards the team actions on a public site:
adding or deleting robot videos and restocking products. Watching and ordering need none.
"""

import hmac

from . import config


class TeamError(Exception):
    """A team action was refused (shown as a message)."""


def enabled():
    return bool(config.UPLOAD_PASSWORD)


def check(password):
    if not config.UPLOAD_PASSWORD:
        raise TeamError("Team actions (robot videos, restock) are switched off on this server: "
                        "set STOREPILOT_UPLOAD_PASSWORD.")
    if not hmac.compare_digest((password or "").encode(), config.UPLOAD_PASSWORD.encode()):
        raise TeamError("Wrong team password.")
