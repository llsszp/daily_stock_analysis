# -*- coding: utf-8 -*-

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import main


class TestMainResourceLimits(unittest.TestCase):
    def test_raises_low_file_descriptor_soft_limit(self):
        fake_resource = SimpleNamespace(
            RLIMIT_NOFILE=8,
            RLIM_INFINITY=-1,
            getrlimit=MagicMock(return_value=(256, 10240)),
            setrlimit=MagicMock(),
        )

        with patch.dict(sys.modules, {"resource": fake_resource}):
            result = main._raise_file_descriptor_limit(4096)

        self.assertEqual(result, (256, 4096))
        fake_resource.setrlimit.assert_called_once_with(8, (4096, 10240))

    def test_keeps_sufficient_existing_limit(self):
        fake_resource = SimpleNamespace(
            RLIMIT_NOFILE=8,
            RLIM_INFINITY=-1,
            getrlimit=MagicMock(return_value=(8192, 10240)),
            setrlimit=MagicMock(),
        )

        with patch.dict(sys.modules, {"resource": fake_resource}):
            result = main._raise_file_descriptor_limit(4096)

        self.assertIsNone(result)
        fake_resource.setrlimit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
