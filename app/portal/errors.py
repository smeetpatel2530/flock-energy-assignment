class PortalError(Exception):
    """Base class for failures talking to the Urja Meter Ops portal."""


class PortalAuthError(PortalError):
    """The portal rejected our credentials or session."""


class PortalSignatureError(PortalError):
    """The portal rejected the signature on a bulk-export request."""


class PortalNotFoundError(PortalError):
    """The portal says the requested resource does not exist."""


class PortalUnavailableError(PortalError):
    """The portal could not be reached, errored, or kept rate-limiting us."""


class PortalTimeoutError(PortalUnavailableError):
    """The portal did not answer within the configured timeout."""


class PortalResponseError(PortalError):
    """The portal answered with something we could not understand."""
