import sys
import types
import pytest
from unittest.mock import patch
from pathlib import Path
import utils.configuration_interpreter as ci


#STUB
@pytest.fixture(autouse=True)
def mock_github_infrastructure():

    with patch("utils.configuration_interpreter.GithubUtil") as mock_cls:
        mock_instance = mock_cls.return_value
        
        global _CURRENT_MOCK_GITHUB
        _CURRENT_MOCK_GITHUB = mock_instance
        
        yield mock_instance


#TOML fixtures


TOML_MINIMAL = """
[task]
deterministic = true

[requirements]
packages = []

[outputs]
dir = "/tmp/out"
filename_pattern = "result_{start}_{end}.jsonl"
"""

TOML_FULL = """
[task]
deterministic = false

[requirements]
packages = ["numpy", "pandas", "requests"]

[download]
[[download.files]]
url  = "https://example.com/model.bin"
dest = "/tmp/model.bin"
post = "chmod +x /tmp/model.bin"

[inputs]
type = "range_continuous"
[inputs.range_continuous]
start = 0
end   = 99
step  = 2

[outputs]
dir              = "/data/results"
filename_pattern = "worker_{worker_id}_{start}_{end}.jsonl"
mode             = "file"

[hash]
exclude = ["timestamp", "run_id"]

[network]
allowed_hosts = ["api.example.com", "cdn.example.com"]
"""

TOML_RANGE_DISCRETE = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "range_discrete"
[inputs.range_discrete]
values = ["alpha", "beta", "gamma"]

[outputs]
dir              = "."
filename_pattern = "out_{start}_{end}.txt"
"""

TOML_FILE_SINGLE_TEXT = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "file_single"
[inputs.file_single]
path       = "data/words.txt"
format     = "text"
delimiter  = "\\n"
skip_header = true

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_FILE_SINGLE_JSON = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "file_single"
[inputs.file_single]
path   = "data/items.json"
format = "json"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_DIRECTORY = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "directory"
[inputs.directory]
path       = "images/"
recursive  = false
extensions = [".png", ".jpg"]
sort_order = "filename"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_FILE_MULTI = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "file_multi"
[inputs.file_multi]
glob   = "data/**/*.csv"
format = "binary"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_DYNAMIC = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "dynamic"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_STDOUT_MODE = """
[task]
deterministic = true

[requirements]
packages = []

[outputs]
dir  = "."
filename_pattern = "*.jsonl"
mode = "stdout"
"""

TOML_INVALID_INPUT_TYPE = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "nonexistent_type"
[inputs.nonexistent_type]
foo = "bar"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_MISSING_SUBSECTION = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]
type = "range_continuous"

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""

TOML_INVALID_OUTPUT_MODE = """
[task]
deterministic = true

[requirements]
packages = []

[outputs]
dir  = "."
filename_pattern = "*.jsonl"
mode = "ftp"
"""

TOML_NO_INPUT_TYPE = """
[task]
deterministic = true

[requirements]
packages = []

[inputs]

[outputs]
dir = "."
filename_pattern = "*.jsonl"
"""


#Helpers


def _load(toml_str: str, url: str = "https://github.com/test/repo"):
    ci._config = None
    _CURRENT_MOCK_GITHUB.read_file.side_effect = None
    _CURRENT_MOCK_GITHUB.read_file.return_value = toml_str
    return ci.load_config(url)


def _load_with_dir(toml_str: str, dir_files=None, glob_files=None,
                   file_content=None, url="https://github.com/test/repo"):
    ci._config = None
    _dir_files    = dir_files  or []
    _glob_files   = glob_files or []
    _file_content = file_content or ""

    def smart_read(path):
        if path == "config.toml" or path == "config.toml":
            return toml_str
        return _file_content

    _CURRENT_MOCK_GITHUB.read_file.side_effect = smart_read
    _CURRENT_MOCK_GITHUB.list_dir.return_value = _dir_files
    _CURRENT_MOCK_GITHUB.list_glob.return_value = _glob_files

    return ci.load_config(url)



#Singleton


class TestSingleton:

    def test_cfg_raises_before_load(self):
        ci._config = None
        with pytest.raises(RuntimeError, match="load_config"):
            ci.cfg()

    def test_cfg_returns_config_after_load(self):
        _load(TOML_MINIMAL)
        assert isinstance(ci.cfg(), ci.Config)

    def test_reload_updates_singleton(self):
        _load(TOML_MINIMAL)
        first = ci.cfg()
        _load(TOML_FULL)
        second = ci.cfg()
        assert first is not second
        assert second.task.deterministic is False



#[task]


class TestTaskConfig:

    def test_deterministic_true(self):
        c = _load(TOML_MINIMAL)
        assert c.task.deterministic is True

    def test_deterministic_false(self):
        c = _load(TOML_FULL)
        assert c.task.deterministic is False

    def test_default_deterministic(self):
        toml = "[requirements]\npackages=[]\n[outputs]\ndir='.'\nfilename_pattern='*'\n"
        c = _load(toml)
        assert c.task.deterministic is True



#[requirements]


class TestRequirementsConfig:

    def test_empty_packages(self):
        c = _load(TOML_MINIMAL)
        assert c.requirements.packages == []

    def test_packages_list(self):
        c = _load(TOML_FULL)
        assert "numpy" in c.requirements.packages
        assert "pandas" in c.requirements.packages
        assert len(c.requirements.packages) == 3



#[download]


class TestDownloadConfig:

    def test_no_download_section(self):
        c = _load(TOML_MINIMAL)
        assert c.download.files == []

    def test_download_file_parsed(self):
        c = _load(TOML_FULL)
        assert len(c.download.files) == 1
        f = c.download.files[0]
        assert f.url  == "https://example.com/model.bin"
        assert f.dest == "/tmp/model.bin"
        assert f.post == "chmod +x /tmp/model.bin"

    def test_download_file_post_default(self):
        toml = TOML_MINIMAL + '\n[[download.files]]\nurl="http://x.com/f"\ndest="/tmp/f"\n'
        c = _load(toml)
        assert c.download.files[0].post == ""



#[outputs]


class TestOutputsConfig:

    def test_dir_and_pattern(self):
        c = _load(TOML_MINIMAL)
        assert c.outputs.dir == "/tmp/out"
        assert c.outputs.filename_pattern == "result_{start}_{end}.jsonl"

    def test_resolve_filename_basic(self):
        c = _load(TOML_FULL)
        p = c.outputs.resolve_filename(0, 9, worker_id=3)
        assert str(p) == "/data/results/worker_3_0_9.jsonl"

    def test_resolve_filename_default_worker(self):
        c = _load(TOML_MINIMAL)
        p = c.outputs.resolve_filename(10, 19)
        assert p.name == "result_10_19.jsonl"

    def test_ensure_dir(self, tmp_path):
        c = _load(TOML_MINIMAL)
        c.outputs.dir = str(tmp_path / "new_dir")
        c.outputs.ensure_dir()
        assert (tmp_path / "new_dir").is_dir()

    def test_default_mode_file(self):
        c = _load(TOML_MINIMAL)
        assert c.outputs.mode == "file"

    def test_mode_stdout(self):
        c = _load(TOML_STDOUT_MODE)
        assert c.outputs.mode == "stdout"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="mode"):
            _load(TOML_INVALID_OUTPUT_MODE)



#[hash] y [network]


class TestHashAndNetwork:

    def test_hash_exclude(self):
        c = _load(TOML_FULL)
        assert "timestamp" in c.hash.exclude
        assert "run_id"    in c.hash.exclude

    def test_hash_exclude_default_empty(self):
        c = _load(TOML_MINIMAL)
        assert c.hash.exclude == []

    def test_network_allowed_hosts(self):
        c = _load(TOML_FULL)
        assert "api.example.com" in c.network.allowed_hosts

    def test_network_default_empty(self):
        c = _load(TOML_MINIMAL)
        assert c.network.allowed_hosts == []



#inputs  range_continuous


class TestInputsRangeContinuous:

    def test_type(self):
        c = _load(TOML_FULL)
        assert c.inputs.type == "range_continuous"

    def test_active_returns_range(self):
        c = _load(TOML_FULL)
        assert isinstance(c.inputs.active_items(), range)

    def test_range_values(self):
        c = _load(TOML_FULL)
        items = list(c.inputs.active_items())
        assert items[0] == 0
        assert items[-1] == 98
        assert all(i % 2 == 0 for i in items)

    def test_total(self):
        c = _load(TOML_FULL)
        assert c.inputs.total() == 50

    def test_range_defaults(self):
        toml = TOML_MINIMAL + "\n[inputs]\ntype='range_continuous'\n[inputs.range_continuous]\nend=4\n"
        c = _load(toml)
        assert list(c.inputs.active_items()) == [0, 1, 2, 3, 4]



#inputs  range_discrete


class TestInputsRangeDiscrete:

    def test_values(self):
        c = _load(TOML_RANGE_DISCRETE)
        assert c.inputs.active_items() == ["alpha", "beta", "gamma"]

    def test_total(self):
        c = _load(TOML_RANGE_DISCRETE)
        assert c.inputs.total() == 3



#inputs  file_single (text)


class TestInputsFileSingleText:

    _file_content = "header\nline1\nline2\nline3\n"

    def test_lines_returned_skip_header(self):
        c = _load_with_dir(TOML_FILE_SINGLE_TEXT, file_content=self._file_content)
        items = c.inputs.active_items()
        assert "header" not in items
        assert "line1" in items
        assert len(items) == 3

    def test_empty_lines_excluded(self):
        content = "header\nline1\n\n  \nline2\n"
        c = _load_with_dir(TOML_FILE_SINGLE_TEXT, file_content=content)
        items = c.inputs.active_items()
        assert "" not in items

    def test_total_matches_items(self):
        c = _load_with_dir(TOML_FILE_SINGLE_TEXT, file_content=self._file_content)
        assert c.inputs.total() == len(c.inputs.active_items())



#inputs  file_single (json)


class TestInputsFileSingleJson:

    _json_content = '["item_a", "item_b", "item_c"]'

    def test_json_parsed(self):
        c = _load_with_dir(TOML_FILE_SINGLE_JSON, file_content=self._json_content)
        assert c.inputs.active_items() == ["item_a", "item_b", "item_c"]

    def test_total(self):
        c = _load_with_dir(TOML_FILE_SINGLE_JSON, file_content=self._json_content)
        assert c.inputs.total() == 3



#inputs  directory


class TestInputsDirectory:

    _dir_files = ["images/a.png", "images/b.jpg", "images/c.png"]

    def test_urls_returned(self):
        c = _load_with_dir(TOML_DIRECTORY, dir_files=self._dir_files)
        items = c.inputs.active_items()
        assert len(items) == 3
        assert all("github.com" in i for i in items)

    def test_total(self):
        c = _load_with_dir(TOML_DIRECTORY, dir_files=self._dir_files)
        assert c.inputs.total() == 3

    def test_no_github_url_raises(self):
        c = _load_with_dir(TOML_DIRECTORY, dir_files=self._dir_files)
        c.inputs.github_url = None
        with pytest.raises(RuntimeError, match="github_url"):
            c.inputs.active_items()



#inputs  file_multi


class TestInputsFileMulti:

    _glob_files = ["data/a.csv", "data/sub/b.csv"]

    def test_urls_returned(self):
        c = _load_with_dir(TOML_FILE_MULTI, glob_files=self._glob_files)
        assert len(c.inputs.active_items()) == 2

    def test_no_github_url_raises(self):
        c = _load_with_dir(TOML_FILE_MULTI, glob_files=self._glob_files)
        c.inputs.github_url = None
        with pytest.raises(RuntimeError, match="github_url"):
            c.inputs.active_items()



#inputs  dynamic


class TestInputsDynamic:

    def test_active_items_empty(self):
        c = _load(TOML_DYNAMIC)
        assert c.inputs.active_items() == []

    def test_active_returns_dynamic_instance(self):
        c = _load(TOML_DYNAMIC)
        assert isinstance(c.inputs.active(), ci.DynamicInput)

    def test_total_zero(self):
        c = _load(TOML_DYNAMIC)
        assert c.inputs.total() == 0



#inputs ausentes


class TestInputsNone:

    def test_inputs_is_none_when_missing(self):
        c = _load(TOML_MINIMAL)
        assert c.inputs is None



#errores de validación


class TestValidationErrors:

    def test_invalid_input_type(self):
        with pytest.raises(ValueError, match="no válido"):
            _load(TOML_INVALID_INPUT_TYPE)

    def test_missing_input_subsection(self):
        with pytest.raises(KeyError):
            _load(TOML_MISSING_SUBSECTION)

    def test_missing_input_type_key(self):
        with pytest.raises(KeyError, match="type"):
            _load(TOML_NO_INPUT_TYPE)

    def test_invalid_output_mode(self):
        with pytest.raises(ValueError, match="mode"):
            _load(TOML_INVALID_OUTPUT_MODE)



#active() edge cases


class TestActiveEdgeCases:

    def test_active_none_type_raises(self):
        inp = ci.InputsConfig(type=None)
        with pytest.raises(ValueError):
            inp.active()

    def test_active_items_none_type_raises(self):
        inp = ci.InputsConfig(type=None)
        with pytest.raises(ValueError):
            inp.active_items()

    def test_active_missing_subsection_raises(self):
        inp = ci.InputsConfig(type="range_continuous", range_continuous=None)
        with pytest.raises(ValueError, match="falta la subsección"):
            inp.active()



#resolve_filename con varios patrones


class TestResolveFilename:

    def test_pattern_with_worker_id(self):
        out = ci.OutputsConfig(dir="/out", filename_pattern="w{worker_id}_{start}-{end}.csv")
        p = out.resolve_filename(5, 10, worker_id=7)
        assert p.name == "w7_5-10.csv"

    def test_pattern_wildcard_unchanged(self):
        out = ci.OutputsConfig(dir="/out", filename_pattern="*")
        p = out.resolve_filename(0, 0)
        assert p.name == "*"

    def test_path_is_path_object(self):
        out = ci.OutputsConfig(dir="/out", filename_pattern="result.jsonl")
        p = out.resolve_filename(0, 0)
        assert isinstance(p, Path)