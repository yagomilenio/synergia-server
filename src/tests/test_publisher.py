import sys
import os
import pytest
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from publisher import Producer, memoria_total, suma_digitos_secuencia


class TestPublisher:

    @pytest.fixture
    def mock_pika(self):
        with patch("publisher.pika") as mock_pika_module:
            mock_channel = MagicMock()
            mock_pika_module.BlockingConnection.return_value.channel.return_value = mock_channel
            yield mock_channel

    def test_init_producer(self, mock_pika):
        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        
        mock_pika.queue_declare.assert_called_once_with(queue="main_queue", durable=True)

    def test_publish_chunks(self, mock_pika):
        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        mock_pika.queue_declare.reset_mock() 

        chunks = producer.publish_chunks(task_id="123", n_inputs=10)

        mock_pika.queue_declare.assert_called_with(queue="task_123", durable=True)
        assert len(chunks) > 0
        assert mock_pika.basic_publish.call_count == len(chunks)

    def test_publish_items_success(self, mock_pika):
        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        items = [{"id": 1, "data": "test"}, {"id": 2, "data": "test2"}]
        
        producer.publish_items(task_id="123", items=items)
        
        assert mock_pika.basic_publish.call_count == len(items)

    def test_publish_items_exceed_size(self, mock_pika):

        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        
        # Creamos un item gigantesco que supere los 65535 bytes
        huge_item = {"large_field": "X" * 70000}
        
        with pytest.raises(ValueError) as exc_info:
            producer.publish_items(task_id="123", items=[huge_item])
            
        assert "ha superado el límite de 65535 bytes" in str(exc_info.value)

    def test_generate_chunks_with_high_memory_adjustment(self, mock_pika):

        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        

        chunks = producer.generate_chunks(task_id="999", n_inputs=500000)
        
        assert len(chunks) > 0

        assert "index" in chunks[0]
        assert "count" in chunks[0]

    def test_delete_rabbit_queue(self, mock_pika):

        producer = Producer("localhost", 5672, "guest", "guest", "main_queue")
        producer.delete_rabbit_queue(task_id="456")
        
        mock_pika.queue_delete.assert_called_once_with(queue="task_456")


class TestMathHelpers:

    def test_suma_digitos_secuencia_no_overlap(self):

        res = suma_digitos_secuencia(start=20, end=10, step=1)
        assert res == 0

    def test_memoria_total_calculo(self):

        res = memoria_total(start=0, end=10, step=2)
        assert res > 0