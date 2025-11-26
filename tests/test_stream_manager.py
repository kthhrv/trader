import unittest
from unittest.mock import patch, MagicMock, call
import subprocess
import json
import time
import os
import threading
from src.stream_manager import StreamManager
from src.ig_client import IGClient

class TestStreamManager(unittest.TestCase):

    def setUp(self):
        self.mock_ig_client = MagicMock(spec=IGClient)
        # Mock authentication to provide necessary tokens
        self.mock_ig_client.authenticated = True
        
        # Mock the service attribute hierarchy manually since MagicMock(spec=IGClient) doesn't autocreate it with attributes we can assign to immediately if not careful
        mock_service = MagicMock()
        mock_service.session.headers = {
            'CST': 'mock_cst',
            'X-SECURITY-TOKEN': 'mock_xst'
        }
        mock_service.account_id = 'mock_account_id'
        self.mock_ig_client.service = mock_service
        
        self.stream_manager = StreamManager(self.mock_ig_client)

    @patch('subprocess.Popen')
    @patch('threading.Thread')
    def test_connect_success(self, mock_thread_cls, mock_popen):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process
        
        # Simulate Node.js reporting connection via stdout
        mock_stdout_pipe = MagicMock()
        # readline is called in a loop
        mock_stdout_pipe.readline.side_effect = [
            '[NODE_STREAM_INFO] [LS Status]: CONNECTED:WS-STREAMING\n',
            '' # End of stream
        ]
        mock_process.stdout = mock_stdout_pipe
        
        # Mock the thread instance
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        # We need to verify that self.stream_manager.is_connected.wait() is called.
        # self.stream_manager.is_connected is a real threading.Event instance.
        # To assert on it, we can patch the `wait` method on the instance.
        
        with patch.object(self.stream_manager.is_connected, 'wait', return_value=True) as mock_wait:
            self.stream_manager.connect()
            
            # Assertions
            mock_popen.assert_called_once()
            cmd_args = mock_popen.call_args[0][0]
            self.assertEqual(cmd_args[0], "node")
            self.assertEqual(cmd_args[2], "mock_cst")
            self.assertEqual(cmd_args[5], "PLACEHOLDER_EPIC")
            
            mock_thread_cls.assert_called_once_with(target=self.stream_manager._read_stdout, args=(mock_stdout_pipe,))
            mock_thread_instance.start.assert_called_once()
            
            mock_wait.assert_called_once_with(timeout=10)

    @patch('subprocess.Popen')
    @patch('threading.Thread')
    def test_connect_timeout(self, mock_thread_cls, mock_popen):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process
        mock_process.poll.return_value = None
        
        mock_stdout_pipe = MagicMock()
        mock_stdout_pipe.readline.return_value = '' # No connection status
        mock_process.stdout = mock_stdout_pipe
        
        mock_thread_instance = MagicMock()
        mock_thread_cls.return_value = mock_thread_instance

        # Simulate timeout behavior
        with patch.object(self.stream_manager.is_connected, 'wait', return_value=False) as mock_wait:
            with patch.object(self.stream_manager, 'stop') as mock_stop:
                self.stream_manager.connect()
                mock_wait.assert_called_once_with(timeout=10)
                mock_stop.assert_called_once()

    @patch('subprocess.Popen')
    @patch('threading.Thread')
    def test_connect_and_subscribe(self, mock_thread_cls, mock_popen):
        mock_process = MagicMock()
        mock_popen.return_value = mock_process
        mock_process.stdout = MagicMock()
        
        # Assume already connected for the sake of flow (though logic restarts)
        # The method calls `stop` (killing old) then `connect_and_subscribe` (spawning new)
        
        # Patch is_connected event to return True immediately on wait
        with patch.object(self.stream_manager.is_connected, 'wait', return_value=True):
             callback = MagicMock()
             self.stream_manager.connect_and_subscribe("TEST_EPIC", callback)
             
             # Check if new process spawned with correct epic
             mock_popen.assert_called_once()
             cmd_args = mock_popen.call_args[0][0]
             self.assertIn("TEST_EPIC", cmd_args)
             self.assertEqual(self.stream_manager.callbacks["TEST_EPIC"], callback)

    @patch('subprocess.Popen')
    def test_read_stdout_processing(self, mock_popen):
        # Test the _read_stdout method directly
        mock_callback = MagicMock()
        self.stream_manager.callbacks["TEST_EPIC"] = mock_callback
        
        # Mock pipe
        mock_pipe = MagicMock()
        
        # JSON line followed by non-JSON line
        price_data = {"type": "price_update", "epic": "TEST_EPIC", "bid": 100, "offer": 101}
        mock_pipe.readline.side_effect = [
            json.dumps(price_data) + '\n',
            '[NODE_STREAM_INFO] Some Info\n',
            ''
        ]
        
        # Execute
        self.stream_manager._read_stdout(mock_pipe)
        
        # Verify callback
        mock_callback.assert_called_once_with(price_data)

if __name__ == '__main__':
    unittest.main()
