"""Pinned production frontend API; not used by the staging deployment unit."""
from staging_api import create_app

app = create_app({'MANAGED_AWG': True, 'TEST_ONLY': False, 'WEB_PUSH': True})

