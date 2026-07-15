from app.domain.git_resolve import GitResolveError, resolve_git_ref


def test_resolve_exact_sha():
    commit, mode = resolve_git_ref("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert mode == "exact"
    assert commit == "a" * 40


def test_resolve_branch_placeholder():
    commit, mode = resolve_git_ref("release/2.1")
    assert mode == "placeholder"
    assert commit == "resolved:release/2.1"


def test_resolve_rejects_bad_ref():
    try:
        resolve_git_ref("../evil")
        assert False
    except GitResolveError:
        pass
