import os

from app.config import load_dotenv


def test_load_dotenv_does_not_override(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comment\nUZEX_T_A=from_file\nexport UZEX_T_B=\"quoted value\"\nUZEX_T_C='x=y'\n"
                   "\nnot a pair\nUZEX_T_SET=from_file\n", encoding="utf-8")
    for k in ("UZEX_T_A", "UZEX_T_B", "UZEX_T_C"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UZEX_T_SET", "from_env")
    load_dotenv(env)
    assert os.environ["UZEX_T_A"] == "from_file"
    assert os.environ["UZEX_T_B"] == "quoted value"
    assert os.environ["UZEX_T_C"] == "x=y"
    assert os.environ["UZEX_T_SET"] == "from_env"
    for k in ("UZEX_T_A", "UZEX_T_B", "UZEX_T_C"):
        monkeypatch.delenv(k)


def test_load_dotenv_missing_file_is_ignored(tmp_path):
    load_dotenv(tmp_path / "nope.env")
