import pika, json
from config import config
import uuid
import math


class Producer():

    def __init__(self, address, port, user, passwd, queue):

        credentials = pika.PlainCredentials(user, passwd)
        connection = pika.BlockingConnection(
            pika.ConnectionParameters(address, port, "/", credentials)
        )
        self.channel = connection.channel()
        self.channel.queue_declare(queue=queue, durable=True)



    def publish_chunks(self, task_id: str, n_inputs: int):

        chunks = self.generate_chunks(task_id, n_inputs)

        queue_name = f"task_{task_id}"
        self.channel.queue_declare(queue=queue_name, durable=True)

        for chunk in chunks:
            self.channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=json.dumps(chunk),
                properties=pika.BasicProperties(delivery_mode=2)
            )

        print(f"[ PRODUCER ] - Se han publicado {len(chunks)} chunks en la cola {queue_name}")
        return chunks

    def publish_items(self, task_id: str, items):
        queue_name = f"task_{task_id}"
        MAX_INPUT_SIZE = 65535 #Bytes
        self.channel.queue_declare(queue=queue_name, durable=True)


        for item in items:
            payload = json.dumps(item)
            encoded_body = payload.encode('utf-8')
            if len(encoded_body) > MAX_INPUT_SIZE:
                raise ValueError(f"Uno de los inputs que ha superado el límite de {MAX_INPUT_SIZE} bytes establecido")
            
            self.channel.basic_publish(
                exchange="",
                routing_key=queue_name,
                body=encoded_body,
                properties=pika.BasicProperties(delivery_mode=2)
            )

        print(f"[ PRODUCER ] - Se han publicado {len(items)} items en la cola {queue_name}")




    def generate_chunks(self, task_id, n_inputs: int):
        chunks = []
        start_index = 0
        chunk_size = 1
        maxima_memoria = 512*1024  #512KB de maximo pilla algo mas pq hay mas overheads de rabbitmq
        
        memoria_requerida  = memoria_total(start_index, n_inputs-1, chunk_size)
        print(f"mem {memoria_requerida}")
        
        while memoria_requerida > maxima_memoria:
            nuevo_chunk_size = int(chunk_size * memoria_requerida / maxima_memoria)    #c/m = c'/512MB  => c'
            chunk_size = max(chunk_size + 1, nuevo_chunk_size)  #evitar que se quede estancado
            print(f"cambio chunk size: {chunk_size}")
            memoria_requerida = memoria_total(start_index, n_inputs-1, chunk_size)
            print(f"mem {memoria_requerida}")


        while start_index < n_inputs:
            count = min(chunk_size, n_inputs - start_index)

            chunks.append({
                "index": start_index,
                "count": count
            })
            
            start_index += count


        return chunks

    def delete_rabbit_queue(self, task_id: str):
        self.channel.queue_delete(queue=f"task_{task_id}")

    

def memoria_total(start, end, step):
    OVERHEAD_AMQP = 3   #amqp añade 3Bytes a mallores en el payload
    base = len('{"index":,"count":}') + OVERHEAD_AMQP
    n = math.ceil((end - start) / step)
    
    # Suma de dígitos de todos los start_index y end_index sin iterar
    suma_start = suma_digitos_secuencia(start, end, step)
    suma_end   = suma_digitos_secuencia(start + step, end + step, step)
    
    return n * base + suma_start + suma_end

def suma_digitos_secuencia(start, end, step):
    """Suma los len(str(x)) para x en range(start, end, step) sin iterar"""
    total = 0
    # Los números de d dígitos van de 10^(d-1) a 10^d - 1
    for digitos in range(1, len(str(end)) + 2):
        tramo_ini = max(start, 10**(digitos-1))
        tramo_fin = min(end,   10**digitos)
        if tramo_ini >= tramo_fin:
            continue
        # Cuántos valores del step caen en este tramo
        count = math.ceil((tramo_fin - tramo_ini) / step)
        total += count * digitos
    return total