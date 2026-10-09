"""Test isolation shared by the whole suite.

The host's heavy-operation lease (akousma.resource_admission) is shared with any running
stack. Tests take their own, so they neither wait behind a live listening nor hold one up.
"""

import os
import tempfile

os.environ.setdefault(
    "LISTENINGSTACK_RESOURCE_DIR", tempfile.mkdtemp(prefix="oida-test-resources-")
)
