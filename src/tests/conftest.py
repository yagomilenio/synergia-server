import pytest
import prometheus_client

@pytest.fixture(autouse=True)
def clean_prometheus_registry():
    """
    Este fixture se ejecuta automáticamente antes de CADA test de la suite.
    Vacíe el registro de Prometheus para evitar los errores de 'Duplicated timeseries'.
    """
    reg = prometheus_client.REGISTRY
    # Recorremos los colectores registrados y los eliminamos
    for collector in list(reg._collector_to_names.keys()):
        reg.unregister(collector)
    yield