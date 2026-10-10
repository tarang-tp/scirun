import pytest

from scirun.results import ResultsNotFound, fetch_results


def test_copies_files(tmp_path):
    root, dest = tmp_path / "root", tmp_path / "dest"
    (root / "demo-abc123" / "sub").mkdir(parents=True)
    (root / "demo-abc123" / "a.png").write_bytes(b"x")
    (root / "demo-abc123" / "sub" / "b.txt").write_text("y")
    files = fetch_results("demo-abc123", root, dest)
    assert [str(f) for f in files] == ["a.png", "sub/b.txt"]
    assert (dest / "sub" / "b.txt").read_text() == "y"


def test_missing_folder(tmp_path):
    with pytest.raises(ResultsNotFound):
        fetch_results("demo-abc123", tmp_path, tmp_path / "out")


def test_empty_folder(tmp_path):
    (tmp_path / "demo-abc123").mkdir()
    with pytest.raises(ResultsNotFound):
        fetch_results("demo-abc123", tmp_path, tmp_path / "out")


@pytest.mark.parametrize("bad", ["../etc", "a/b", "", "UPPER", "-x"])
def test_rejects_unsafe_job_ids(tmp_path, bad):
    with pytest.raises(ResultsNotFound):
        fetch_results(bad, tmp_path, tmp_path / "out")
