import requests 
import base64
from urllib.parse import quote

class GithubUtil:
    def __init__(self, url):
        self.url = url.rstrip("/")
        parts = self.url.replace("https://github.com/", "").split("/")
        if len(parts) < 2:
            raise ValueError("URL de GitHub inválida")
        self.owner = parts[0]
        self.repo = parts[1]

        if len(parts) >= 4 and parts[2] == "tree":
            self.branch = parts[3]
        else:
            self.branch = None
        self.api_base = f"https://api.github.com/repos/{self.owner}/{self.repo}"

    def list_dir(self, path=""):
        url = f"{self.api_base}/contents/{quote(path)}"
        if self.branch:
            url += f"?ref={quote(self.branch)}"
        resp = requests.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al listar {path}: {resp.status_code}")
        items = resp.json()
        return [item["name"] for item in items]

    def read_file(self, path):
        url = f"{self.api_base}/contents/{quote(path)}"
        if self.branch:
            url += f"?ref={quote(self.branch)}"

        print(url)
        resp = requests.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al leer {path}: {resp.status_code}")
        content = resp.json().get("content", "")
        return base64.b64decode(content).decode("utf-8")

    def get_repo_link(self):
        return f"https://github.com/{self.owner}/{self.repo}"

    def get_branch(self):
        return self.branch

    def get_commit(self):
        url = f"{self.api_base}/commits"
        if self.branch:
            url += f"?sha={quote(self.branch)}&per_page=1"
        else:
            url += "?per_page=1"
        resp = requests.get(url)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al obtener commit: {resp.status_code}")
        return resp.json()[0]["sha"]