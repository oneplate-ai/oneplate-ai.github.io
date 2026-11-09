import tempfile
import traceback
import unittest
from unittest.mock import patch

import blogger_draft_create as create


class SecretRegression(unittest.TestCase):
    def test_create_error_does_not_expose_transport_exception_or_chain(self):
        with tempfile.TemporaryDirectory() as state:
            with patch.object(create, 'GENERIC_STATE_ROOT', __import__('pathlib').Path(state)), patch.object(create, '_inputs', side_effect=RuntimeError('secret-access-token')):
                try:
                    create.run(None, '123', '/manifest', 'a' * 64, state)
                except create.CreateError as error:
                    rendered = ''.join(traceback.format_exception(error))
                    self.assertNotIn('secret-access-token', rendered)
                else:
                    self.fail('expected CreateError')
