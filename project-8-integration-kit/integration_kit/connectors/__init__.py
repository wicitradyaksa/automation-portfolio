"""One module per source. Each one owns exactly two responsibilities:

1. speak that API's auth dialect;
2. map its payload into :class:`integration_kit.schema.Contact`.

They share no state and do not import each other. Adding a fourth source means
adding a file here and a line in the sync, not editing three existing ones.
"""

from .crm import CrmConnector
from .billing import BillingWebhookConnector
from .helpdesk import HelpdeskConnector

__all__ = ["CrmConnector", "BillingWebhookConnector", "HelpdeskConnector"]
