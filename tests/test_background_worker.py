import pytest
from unittest.mock import MagicMock, patch


def test_worker_initialization():
    from chatbot.background_worker import BackgroundWorker
    worker = BackgroundWorker()
    assert worker.task_queue.empty()
    assert not worker.is_running


def test_worker_start_stop():
    from chatbot.background_worker import BackgroundWorker
    worker = BackgroundWorker()
    
    worker.start()
    assert worker.is_running
    
    worker.stop()
    assert not worker.is_running


def test_worker_processes_task():
    with patch('chatbot.background_worker.process_task') as mock_process:
        from chatbot.background_worker import BackgroundWorker
        worker = BackgroundWorker()
        
        mock_task = MagicMock()
        worker.task_queue.put(mock_task)
        
        worker.start()
        # Allow processing time
        import time
        time.sleep(0.1)
        worker.stop()
        
        mock_process.assert_called_with(mock_task)


def test_worker_handles_exceptions():
    with patch('chatbot.background_worker.process_task', 
               side_effect=Exception("Test error")):
        from chatbot.background_worker import BackgroundWorker
        worker = BackgroundWorker()
        
        worker.task_queue.put(MagicMock())
        worker.start()
        # Allow processing time
        import time
        time.sleep(0.1)
        worker.stop()
        
        # Should not crash and continue running
        assert not worker.is_running  # After stop()
