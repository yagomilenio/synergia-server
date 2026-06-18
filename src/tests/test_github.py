import pytest
import base64
from unittest.mock import patch, MagicMock
from utils.github_util import GithubUtil


URL_BASE = "https://github.com/yagomilenio/testRepositoryForParallel"
URL_CON_BARRA = "https://github.com/yagomilenio/testRepositoryForParallel/"
URL_CON_RAMA = "https://github.com/yagomilenio/testRepositoryForParallel/tree/main"
URL_RAMA_DEV = "https://github.com/yagomilenio/testRepositoryForParallel/tree/dev"
URL_INVALIDA = "https://github.com/solo-usuario"

OWNER_ESPERADO = "yagomilenio"
REPO_ESPERADO = "testRepositoryForParallel"
API_BASE_ESPERADA = "https://api.github.com/repos/yagomilenio/testRepositoryForParallel"

TEXTO_PRUEBA = "¡Contenido de prueba!"
B64_PRUEBA = base64.b64encode(TEXTO_PRUEBA.encode("utf-8")).decode("utf-8")

# Mocks de respuestas de la API de GitHub
MOCK_JSON_DIR = [
    {"name": "src", "type": "dir"},
    {"name": "README.md", "type": "file"},
    {"name": "main.py", "type": "file"}
]
RESULTADO_DIR_ESPERADO = ["src", "README.md", "main.py"]

MOCK_JSON_FILE = {"content": B64_PRUEBA}

MOCK_JSON_COMMITS_BASE = [{"sha": "a1b2c3d4e5f6g7h8i9j0abcdef1234567890ffff"}]
MOCK_JSON_COMMITS_DEV = [{"sha": "9999999999999999999999999999999999999999"}]



class TestGithubUtilInit:

    def test_parse_url_without_branch(self):
        git = GithubUtil(URL_BASE)
        assert git.owner == OWNER_ESPERADO
        assert git.repo == REPO_ESPERADO
        assert git.branch is None
        assert git.api_base == API_BASE_ESPERADA

    def test_parse_url_with_trailing_slash(self):
        git = GithubUtil(URL_CON_BARRA)
        assert git.owner == OWNER_ESPERADO
        assert git.repo == REPO_ESPERADO
        assert git.branch is None

    def test_parse_url_with_branch(self):
        git = GithubUtil(URL_CON_RAMA)
        assert git.owner == OWNER_ESPERADO
        assert git.repo == REPO_ESPERADO
        assert git.branch == "main"

    def test_invalid_url_raises_value_error(self):
        with pytest.raises(ValueError, match="URL de GitHub inválida"):
            GithubUtil(URL_INVALIDA)



class TestGithubUtilGetters:

    def test_get_repo_link(self):
        git = GithubUtil(URL_RAMA_DEV)
        assert git.get_repo_link() == URL_BASE

    def test_get_branch(self):
        git_with_branch = GithubUtil(URL_CON_RAMA)
        assert git_with_branch.get_branch() == "main"

        git_no_branch = GithubUtil(URL_BASE)
        assert git_no_branch.get_branch() is None



class TestGithubUtilApiInteractions:

    @patch("utils.github_util.requests.get")
    def test_list_dir_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_JSON_DIR
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_CON_RAMA)
        archivos = git.list_dir("mi_carpeta")

        assert archivos == RESULTADO_DIR_ESPERADO
        mock_get.assert_called_once_with(f"{API_BASE_ESPERADA}/contents/mi_carpeta?ref=main")

    @patch("utils.github_util.requests.get")
    def test_list_dir_error_raises_runtime_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_BASE)
        with pytest.raises(RuntimeError, match="Error al listar"):
            git.list_dir("carpeta_fantasma")

    @patch("utils.github_util.requests.get")
    def test_read_file_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_JSON_FILE
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_BASE)
        resultado = git.read_file("README.md")

        assert resultado == TEXTO_PRUEBA
        mock_get.assert_called_once_with(f"{API_BASE_ESPERADA}/contents/README.md")

    @patch("utils.github_util.requests.get")
    def test_read_file_error_raises_runtime_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_BASE)
        with pytest.raises(RuntimeError, match="Error al leer"):
            git.read_file("secret.txt")

    @patch("utils.github_util.requests.get")
    def test_get_commit_without_branch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_JSON_COMMITS_BASE
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_BASE)
        sha = git.get_commit()

        assert sha == MOCK_JSON_COMMITS_BASE[0]["sha"]
        mock_get.assert_called_once_with(f"{API_BASE_ESPERADA}/commits?per_page=1")

    @patch("utils.github_util.requests.get")
    def test_get_commit_with_branch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = MOCK_JSON_COMMITS_DEV
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_RAMA_DEV)
        sha = git.get_commit()

        assert sha == MOCK_JSON_COMMITS_DEV[0]["sha"]
        mock_get.assert_called_once_with(f"{API_BASE_ESPERADA}/commits?sha=dev&per_page=1")

    @patch("utils.github_util.requests.get")
    def test_get_commit_error_raises_runtime_error(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_BASE)
        with pytest.raises(RuntimeError, match="Error al obtener commit"):
            git.get_commit()


    @patch("utils.github_util.requests.get")
    def test_read_file_with_branch(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"content": B64_PRUEBA}
        mock_get.return_value = mock_resp

        git = GithubUtil(URL_CON_RAMA)

        git.read_file("README.md")

        mock_get.assert_called_once_with(
            f"{API_BASE_ESPERADA}/contents/README.md?ref=main"
        )


